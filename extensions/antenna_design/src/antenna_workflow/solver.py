"""Adapt the reviewed pymininec public API; keep untyped solver objects here."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from itertools import combinations

import numpy as np
from mininec import mininec

from .contracts import (
    Currents,
    CurrentSample,
    Direction,
    EngineeringRequest,
    EngineeringSummary,
    FarField,
    FieldSample,
    Impedance,
    Point,
    Segment,
)


@dataclass(frozen=True)
class Solution:
    summary: EngineeringSummary
    field: FarField
    currents: Currents
    native_log: str
    native_arguments: str


def _point(values: Sequence[float]) -> Point:
    return float(values[0]), float(values[1]), float(values[2])


def _check_intersections(request: EngineeringRequest) -> None:
    """Reject interior contacts: MININEC only connects explicitly shared endpoints."""
    for left, right in combinations(request.wires, 2):
        a, b = np.asarray(left.start_m), np.asarray(right.start_m)
        u, v = np.subtract(left.end_m, a), np.subtract(right.end_m, b)
        shared = {left.start_m, left.end_m} & {right.start_m, right.end_m}
        if shared:
            # A shared endpoint is valid only if the wires do not overlap along it.
            origin = np.asarray(next(iter(shared)))
            lu = (np.asarray(left.end_m) if left.start_m in shared else a) - origin
            rv = (np.asarray(right.end_m) if right.start_m in shared else b) - origin
            cosine = float(np.dot(lu, rv) / (np.linalg.norm(lu) * np.linalg.norm(rv)))
            if cosine > 1 - 1e-10:
                raise ValueError("Overlapping wires at a shared endpoint are unsupported")
            continue
        radius = left.radius_m + right.radius_m
        matrix = np.column_stack((u, -v))
        distances = []
        for point, start, direction in ((a, b, v), (a + u, b, v), (b, a, u), (b + v, a, u)):
            fraction = float(np.clip(np.dot(point - start, direction) / np.dot(direction, direction), 0, 1))
            distances.append(float(np.linalg.norm(point - start - fraction * direction)))
        parameters, _, rank, _ = np.linalg.lstsq(matrix, b - a, rcond=None)
        if rank == 2 and np.all((parameters >= 0) & (parameters <= 1)):
            distances.append(float(np.linalg.norm(a + parameters[0] * u - b - parameters[1] * v)))
        if min(distances) <= radius:
            raise ValueError("Conductors intersect/touch without an explicit shared endpoint; split the geometry first")


def solve(request: EngineeringRequest) -> Solution:
    """Perform exactly one solve. The caller owns process deadlines and storage."""
    if version("pymininec") != "1.2.0":
        raise ValueError("This adapter requires the reviewed pymininec 1.2.0 API")
    _check_intersections(request)
    wires = [
        mininec.Wire(request.mesh.segments_per_wire, *wire.start_m, *wire.end_m, wire.radius_m)
        for wire in request.wires
    ]
    if request.mesh.mode == "both_end_taper":
        for wire, physical in zip(wires, request.wires, strict=True):
            length = math.dist(physical.start_m, physical.end_m)
            wire.segtype = 3
            wire.taper_min = max(4 * physical.radius_m, length / 512)
            wire.taper_max = length / 16
    media = None
    if request.ground.mode == "ideal":
        media = [mininec.Medium(0, 0)]
    elif request.ground.mode == "homogeneous_soil":
        media = [mininec.Medium(request.ground.relative_permittivity, request.ground.conductivity_s_per_m)]
    model = mininec.Mininec(request.frequency_hz / 1e6, wires, media=media)
    if request.mesh.mode == "both_end_taper" and any(wire.segtype != 3 for wire in wires):
        raise ValueError("Solver fell back to uniform segmentation; no mislabeled tapered result will be produced")
    if not 1 <= len(model.pulses) <= request.max_pulses:
        raise ValueError("Actual pulse count exceeds the admitted budget")
    # Native endpoint tolerance must not introduce junctions/ground contacts absent from the request.
    expected_junctions = {
        (i + 1, j + 1)
        for i, left in enumerate(request.wires)
        for j, right in enumerate(request.wires)
        if i < j and {left.start_m, left.end_m} & {right.start_m, right.end_m}
    }
    actual_junctions = {
        tuple(sorted({int(wire.n) + 1 for wire in pulse.geo}))
        for pulse in model.pulses
        if len({int(wire.n) for wire in pulse.geo}) > 1
    }
    if expected_junctions != actual_junctions or any(any(wire.is_ground) for wire in wires):
        raise ValueError("Solver connectivity differs from the explicit physical topology")
    matches = [
        i
        for i, pulse in enumerate(model.pulses)
        if np.allclose(pulse.point, request.feed_position(), rtol=0, atol=1e-12)
        and request.feed.wire in {int(wire.n) + 1 for wire in pulse.geo}
    ]
    if len(matches) != 1:
        raise ValueError("Requested physical feed must map to exactly one pulse; no nearest-pulse approximation")
    feed_index = matches[0]
    source = mininec.Excitation(request.feed.voltage_v, request.feed.phase_deg)
    model.register_source(source, feed_index)
    if request.material.conductivity_s_per_m is not None:
        for wire in wires:
            model.register_load(mininec.Skin_Effect_Load(wire, conductivity=request.material.conductivity_s_per_m))
    if request.feed.resistance_ohm:
        model.register_load(mininec.Impedance_Load(complex(request.feed.resistance_ohm)), feed_index)
    model.compute()
    z = complex(source.impedance)
    if not (math.isfinite(z.real) and math.isfinite(z.imag) and z.real > 0):
        raise ValueError("Non-finite/non-passive source impedance; result cannot be accepted")
    reflection = abs((z - request.reference_impedance_ohm) / (z + request.reference_impedance_ohm))
    if not 0 <= reflection < 1:
        raise ValueError("SWR cannot be represented as a finite passive result")
    step = request.pattern.step_deg
    theta = mininec.Angle(0, step, (180 if media is None else 90) // step + 1)
    phi = mininec.Angle(0, step, 360 // step)
    model.compute_far_field(theta, phi)
    raw = model.far_field
    vertical, horizontal, total = raw.gain.T
    peak = float(np.max(total))
    if not math.isfinite(peak) or peak <= -999:
        raise ValueError("No finite above-floor far-field samples")
    field = FarField(
        frequency_hz=request.frequency_hz,
        normalized_reference_peak_dbi=peak,
        samples=tuple(
            FieldSample(
                theta_deg=float(t),
                phi_deg=float(p),
                total_gain_dbi=float(g),
                vertical_gain_dbi=float(v),
                horizontal_gain_dbi=float(h),
                normalized_gain_db=float(g - peak) if g != -999 else None,
            )
            for t, p, v, h, g in zip(
                raw.zen.flat, raw.azi.flat, vertical.flat, horizontal.flat, total.flat, strict=True
            )
        ),
    )
    currents = Currents(
        feed_index_zero_based=feed_index,
        samples=tuple(
            CurrentSample(
                position_m=_point(pulse.point),
                adjacent_wires=tuple(sorted({int(wire.n) + 1 for wire in pulse.geo})),
                real_a=float(current.real),
                imag_a=float(current.imag),
                basis_signs=tuple(int(sign) for sign in pulse.sign),
            )
            for pulse, current in zip(model.pulses, model.current, strict=True)
        ),
        segments_by_wire=tuple(
            tuple(Segment(start_m=_point(segment.p1), end_m=_point(segment.p2)) for segment in wire.segments)
            for wire in wires
        ),
    )
    directions = sorted(
        {
            (row.theta_deg, 0 if row.theta_deg in (0, 180) else row.phi_deg)
            for row in field.samples
            if abs(row.total_gain_dbi - peak) < 1e-8
        }
    )
    front = request.pattern.front_phi_deg
    front_back = None
    definition = "Undefined: no explicit front direction supplied"
    if front is not None:
        values = {(row.theta_deg, row.phi_deg): row.total_gain_dbi for row in field.samples}
        theta_cut = 90 - request.pattern.azimuth_cut_elevation_deg
        f, b = values[theta_cut, front], values[theta_cut, (front + 180) % 360]
        if f != -999 and b != -999:
            front_back = f - b
        definition = f"G(theta={theta_cut},phi={front})-G(theta={theta_cut},phi={(front + 180) % 360}); null if floored"
    warnings = [
        "Numerical candidate only: no mesh convergence, physical measurement or MMANA compatibility acceptance",
        "Bare homogeneous round conductors only; insulation, joints, feedline/common-mode and supports are not modeled",
        "Peak and F/B use the sampled grid; no exact beamwidth, sidelobe or bandwidth claim",
    ]
    wavelength = 299800000 / request.frequency_hz
    if any(math.dist(s.start_m, s.end_m) > wavelength / 10 for wire in currents.segments_by_wire for s in wire):
        warnings.append("Some segments exceed wavelength/10; refine the mesh before engineering interpretation")
    if media is not None:
        warnings.append(
            "MININEC ground approximation; low-height impedance and real installation require independent checks"
        )
    if request.material.mode != "ideal":
        warnings.append(
            "Distributed complex skin impedance is not the historical MMANA copper lumped-resistance preset"
        )
    summary = EngineeringSummary(
        frequency_hz=request.frequency_hz,
        impedance_ohm=Impedance(r=z.real, x=z.imag),
        swr=(1 + reflection) / (1 - reflection),
        reference_impedance_ohm=request.reference_impedance_ohm,
        pulse_count=len(currents.samples),
        sampled_peak_gain_dbi=peak,
        peak_direction_count=len(directions),
        peak_direction_examples=tuple(Direction(theta_deg=t, phi_deg=p) for t, p in directions[:8]),
        front_to_back_db=front_back,
        front_to_back_definition=definition,
        warnings=tuple(warnings),
    )
    return Solution(
        summary,
        field,
        currents,
        str(model.as_mininec(options={"far-field"})),
        str(model.as_cmdline(azi=phi, zen=theta)),
    )
