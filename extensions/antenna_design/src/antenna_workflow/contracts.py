"""Explicit units and bounded inputs for one thin-wire calculation.

Solver settings describe a numerical experiment, not a certified construction
design. Unknown fields are rejected to catch accidentally discarded constraints.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictInt, model_validator

type Positive = Annotated[FiniteFloat, Field(gt=0)]
type Point = tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Wire(Contract):
    start_m: Point
    end_m: Point
    radius_m: Positive

    @model_validator(mode="after")
    def thin_wire(self) -> Self:
        if math.dist(self.start_m, self.end_m) <= 20 * self.radius_m:
            raise ValueError("Wire length must exceed 20 radii; surface/short thick conductors are unsupported")
        return self


class Material(Contract):
    mode: Literal["ideal", "physical_conductivity"]
    conductivity_s_per_m: Positive | None = None
    basis: Annotated[str, Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def conductivity(self) -> Self:
        if (self.mode == "physical_conductivity") != (self.conductivity_s_per_m is not None):
            raise ValueError("Physical material requires conductivity; ideal material must not specify it")
        return self


class Ground(Contract):
    mode: Literal["free_space", "ideal", "homogeneous_soil"]
    relative_permittivity: Annotated[FiniteFloat, Field(ge=1)] | None = None
    conductivity_s_per_m: Positive | None = None

    @model_validator(mode="after")
    def soil(self) -> Self:
        values = (self.relative_permittivity, self.conductivity_s_per_m)
        if self.mode == "homogeneous_soil" and any(value is None for value in values):
            raise ValueError("Soil requires relative permittivity and conductivity in S/m")
        if self.mode != "homogeneous_soil" and any(value is not None for value in values):
            raise ValueError("Soil parameters only apply to homogeneous_soil")
        return self


class Feed(Contract):
    wire: Annotated[StrictInt, Field(ge=1)]
    position: Literal["center", "end"]
    voltage_v: Positive = 1
    phase_deg: FiniteFloat = 0
    resistance_ohm: Annotated[FiniteFloat, Field(ge=0)] = 0


class Mesh(Contract):
    mode: Literal["uniform", "both_end_taper"]
    segments_per_wire: Annotated[StrictInt, Field(ge=4, le=256)]

    @model_validator(mode="after")
    def even_segments(self) -> Self:
        if self.segments_per_wire % 2:
            raise ValueError("This subset requires even segment counts to preserve centre feeds")
        return self


class Pattern(Contract):
    # These divisors of 90 include both poles and the horizon without interpolation.
    step_deg: Literal[2, 5, 10, 15, 30] = 5
    azimuth_cut_elevation_deg: Annotated[StrictInt, Field(ge=0, le=90)] = 0
    elevation_cut_phi_deg: Annotated[StrictInt, Field(ge=0, lt=180)] = 90
    front_phi_deg: Annotated[StrictInt, Field(ge=0, lt=360)] | None = None

    @model_validator(mode="after")
    def cuts_on_grid(self) -> Self:
        for angle in (self.azimuth_cut_elevation_deg, self.elevation_cut_phi_deg, self.front_phi_deg):
            if angle is not None and angle % self.step_deg:
                raise ValueError("Cut/front angles must lie on the requested angular grid")
        return self


class EngineeringRequest(Contract):
    schema_version: Literal[1] = 1
    name: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9 _.-]+$")]
    frequency_hz: Annotated[FiniteFloat, Field(ge=1e6, le=1e9)]
    wires: Annotated[tuple[Wire, ...], Field(min_length=1, max_length=16)]
    feed: Feed
    material: Material
    ground: Ground
    mesh: Mesh
    pattern: Pattern = Field(default_factory=Pattern)
    reference_impedance_ohm: Positive = 50
    max_pulses: Annotated[StrictInt, Field(ge=4, le=512)] = 512

    @model_validator(mode="after")
    def supported_geometry(self) -> Self:
        if self.feed.wire > len(self.wires):
            raise ValueError("Feed wire is outside the wire list")
        if len(self.wires) * self.mesh.segments_per_wire > self.max_pulses:
            raise ValueError("Requested mesh exceeds the pulse budget before solver construction")
        if len({wire.radius_m for wire in self.wires}) != 1:
            raise ValueError("Mixed wire radii are outside the reviewed subset")
        if self.ground.mode != "free_space" and any(
            point[2] <= wire.radius_m for wire in self.wires for point in (wire.start_m, wire.end_m)
        ):
            raise ValueError("Ground-connected or buried conductors are outside this subset")
        endpoints: dict[Point, list[str]] = {}
        for wire in self.wires:
            endpoints.setdefault(wire.start_m, []).append("start")
            endpoints.setdefault(wire.end_m, []).append("end")
        if any(len(sides) > 2 or len(sides) == 2 and set(sides) != {"start", "end"} for sides in endpoints.values()):
            raise ValueError("Only consistently directed, unbranched endpoint junctions are supported")
        wire = self.wires[self.feed.wire - 1]
        if self.feed.position == "end" and len(endpoints[wire.end_m]) != 2:
            raise ValueError("End feed must be a physical junction, not an open-end pulse approximation")
        return self

    def feed_position(self) -> Point:
        wire = self.wires[self.feed.wire - 1]
        if self.feed.position == "end":
            return wire.end_m
        a, b = wire.start_m, wire.end_m
        return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2


class Impedance(Contract):
    r: FiniteFloat
    x: FiniteFloat


class Direction(Contract):
    theta_deg: FiniteFloat
    phi_deg: FiniteFloat


class FieldSample(Direction):
    total_gain_dbi: FiniteFloat
    vertical_gain_dbi: FiniteFloat
    horizontal_gain_dbi: FiniteFloat
    normalized_gain_db: FiniteFloat | None


class FarField(Contract):
    frequency_hz: Positive
    coordinate_system: Literal["theta_from_+Z_phi_from_+X_toward_+Y"] = "theta_from_+Z_phi_from_+X_toward_+Y"
    angle_unit: Literal["degree"] = "degree"
    gain_unit: Literal["dBi"] = "dBi"
    polarization: Literal["vertical=theta; horizontal=phi"] = "vertical=theta; horizontal=phi"
    solver_floor_marker: Literal[-999] = -999
    normalized_reference_peak_dbi: FiniteFloat
    samples: tuple[FieldSample, ...]


class CurrentSample(Contract):
    position_m: Point
    adjacent_wires: tuple[int, ...]
    real_a: FiniteFloat
    imag_a: FiniteFloat
    basis_signs: tuple[int, ...]


class Segment(Contract):
    start_m: Point
    end_m: Point


class Currents(Contract):
    feed_index_zero_based: int
    samples: tuple[CurrentSample, ...]
    segments_by_wire: tuple[tuple[Segment, ...], ...]


class ArtifactRef(Contract):
    path: str
    media_type: str
    size_bytes: int
    sha256: str


class EngineeringSummary(Contract):
    status: Literal["COMPUTED_NOT_VALIDATED"] = "COMPUTED_NOT_VALIDATED"
    frequency_hz: Positive
    impedance_ohm: Impedance
    swr: Positive
    reference_impedance_ohm: Positive
    pulse_count: int
    sampled_peak_gain_dbi: FiniteFloat
    peak_direction_count: int
    peak_direction_examples: tuple[Direction, ...]
    front_to_back_db: FiniteFloat | None
    front_to_back_definition: str
    warnings: tuple[str, ...]
    solver_runs: Literal[1] = 1
    llm_calls: Literal[0] = 0
    artifacts: tuple[ArtifactRef, ...] = ()
