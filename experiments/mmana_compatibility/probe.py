"""Capture one fixed pymininec case for an independent MMANA-GAL comparison.

This is a numerical probe, not a solver adapter or an MMANA parser. The fixed
input avoids configuration drift while a human captures the reference model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
from importlib import metadata
from pathlib import Path

import numpy as np
from mininec import mininec


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run(
    output: Path,
    *,
    case_id: str = "A01-uniform20",
    start: tuple[float, float, float] = (-0.5, 0.0, 0.0),
    end: tuple[float, float, float] = (0.5, 0.0, 0.0),
    source_position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    segments: int = 20,
    taper_min_m: float | None = None,
) -> dict[str, object]:
    if metadata.version("pymininec") != "1.2.0":
        raise ValueError("This probe requires pymininec==1.2.0; review its output API before changing the pin.")
    output.mkdir(parents=True, exist_ok=False)
    case = {
        "case_id": case_id,
        "frequency_hz": 145_000_000,
        "wire_start_m": list(start),
        "wire_end_m": list(end),
        "radius_m": 0.001,
        "segments": segments,
        "segmentation": "uniform" if taper_min_m is None else "taper_both_ends",
        "taper_min_m": taper_min_m,
        "taper_max_m": None,
        "source_position_m": list(source_position),
        "source_voltage_magnitude_v": 1.0,
        "source_phase_deg": 0.0,
        "ground": "free_space",
        "conductor": "perfect",
        "loads": [],
        "reference_impedance_ohm": 50.0,
        "theta_deg": {"start": 0, "step": 5, "count": 37},
        "phi_deg": {"start": 0, "step": 5, "count": 72},
    }
    write_json(output / "input.json", case)
    wire = mininec.Wire(segments, *start, *end, 0.001)
    if taper_min_m is not None:
        # These are the same public parameters used by pymininec's CLI.
        # The caller records how the segment count was obtained; this does
        # not derive an automatic MMANA mesh from DM1/DM2/SC/EC.
        wire.segtype = 3
        wire.taper_min = taper_min_m
    model = mininec.Mininec(145.0, [wire], media=None)
    if taper_min_m is not None and wire.segtype != 3:
        raise ValueError("Solver fell back to a uniform mesh; do not label it as tapered.")
    points = np.asarray([pulse.point for pulse in model.pulses])
    matches = np.flatnonzero(np.all(np.isclose(points, source_position, rtol=0.0, atol=1e-12), axis=1))
    if len(points) != segments - 1 or len(matches) != 1:
        raise ValueError("Unexpected pulse layout; do not approximate the requested excitation position.")
    source_index = int(matches[0])
    source = mininec.Excitation(1.0, 0.0)
    model.register_source(source, source_index)
    model.compute()
    model.compute_far_field(mininec.Angle(0, 5, 37), mininec.Angle(0, 5, 72))

    # Read the version-pinned result arrays; never reconstruct data from a plot.
    field = model.far_field
    vertical, horizontal, total = field.gain.T
    samples = [
        {
            "theta_deg": float(theta),
            "phi_deg": float(phi),
            "total_gain_dbi": float(gain),
            "vertical_gain_dbi": float(v),
            "horizontal_gain_dbi": float(h),
        }
        for theta, phi, gain, v, h in zip(
            field.zen.flat, field.azi.flat, total.flat, vertical.flat, horizontal.flat, strict=True
        )
    ]
    valid_gains = [sample["total_gain_dbi"] for sample in samples if sample["total_gain_dbi"] != -999.0]
    peak = max(valid_gains)
    write_json(
        output / "far_field.json",
        {
            "frequency_hz": 145_000_000,
            "coordinate_system": "right-handed; theta from +Z; phi from +X toward +Y; degrees",
            "gain_unit": "dBi",
            "polarization": "pymininec V=theta, H=phi; GAL mapping not yet verified",
            "solver_floor_marker": -999.0,
            "floor_meaning": "below solver numerical floor, not a physical gain of -999 dBi",
            "samples": samples,
        },
    )
    write_json(
        output / "currents.json",
        {
            "pulse_index_base": 0,
            "source_index": source_index,
            "source_position_m": points[source_index].tolist(),
            "segments": [
                {"segment_index": index, "start_m": segment.p1.tolist(), "end_m": segment.p2.tolist()}
                for index, segment in enumerate(wire.segments)
            ],
            "samples": [
                {
                    "pulse_index": index,
                    "position_m": point.tolist(),
                    "real_a": float(current.real),
                    "imag_a": float(current.imag),
                }
                for index, (point, current) in enumerate(zip(points, model.current, strict=True))
            ],
        },
    )
    # Keep the solver's own export to audit this small numerical extractor.
    (output / "pymininec.out").write_text(model.as_mininec() + "\n", encoding="utf-8")
    z = complex(source.impedance)
    reflection = abs((z - 50.0) / (z + 50.0))
    if not 0 <= reflection < 1 or not math.isfinite(peak):
        raise ValueError("Unexpected baseline result; no compatibility judgement can be made.")
    report: dict[str, object] = {
        "case_id": case["case_id"],
        "candidate_status": "computed",
        "mmana_comparison": "AWAITING_REFERENCE",
        "compatibility_pass": None,
        "impedance_ohm": {"r": z.real, "x": z.imag},
        "swr_z0_50": (1 + reflection) / (1 - reflection),
        "sampled_peak_gain_dbi": peak,
        "peak_directions_deg": [
            {"theta": sample["theta_deg"], "phi": sample["phi_deg"]}
            for sample in samples
            if abs(sample["total_gain_dbi"] - peak) < 1e-9
        ],
        "pulse_count": len(points),
        "source_index_zero_based": source_index,
        "far_field_sample_count": len(samples),
        "solver_runs": 1,
        "llm_calls": 0,
        "limits": [
            "No MMANA import/export verification",
            "No mesh-convergence claim",
            "No ground, loss or load coverage; no general MMANA automatic-mesh mapping",
            "No numerical acceptance thresholds applied",
        ],
    }
    write_json(output / "candidate.json", report)
    write_json(
        output / "environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "dependencies": {item.metadata["Name"]: item.version for item in metadata.distributions()},
            "probe_sha256": digest(Path(__file__)),
            "solver_source_sha256": digest(Path(mininec.__file__)),
        },
    )
    write_json(
        output / "manifest.json", {path.name: digest(path) for path in sorted(output.iterdir()) if path.is_file()}
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory; existing paths are refused")
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
