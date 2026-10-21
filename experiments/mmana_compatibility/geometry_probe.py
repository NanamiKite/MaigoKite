"""Evaluate three fixed connected/parasitic structures through public pymininec APIs.

Nine solves measure native mesh changes without tuning dimensions to GAL results.
Raw arrays are artifacts; compact.json contains only engineering summaries.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import shutil
import time
from dataclasses import asdict
from importlib import metadata
from pathlib import Path

import numpy as np
from compare import FLOOR, plt
from engineering_probe import SOLVER_SHA256
from mininec import mininec
from probe import digest, write_json
from windows.recipes import GEOMETRY_CASES, Case

MESHES = (20, 40, 80)
Field = dict[tuple[float, float], list[float]]


def field_summary(field: Field, front_phi: int | None) -> dict:
    peak = max(value[2] for value in field.values())
    # All azimuths at a pole describe one physical direction, not 72 different lobes.
    directions = sorted(
        {(t, 0 if t in (0, 180) else p) for (t, p), value in field.items() if abs(value[2] - peak) < 1e-8}
    )
    front_back = None
    if front_phi is not None:
        front, back = field[(90, front_phi)][2], field[(90, (front_phi + 180) % 360)][2]
        if front != FLOOR and back != FLOOR:
            front_back = front - back
    return {
        "sampled_peak_gain_dbi": peak,
        "sampled_peak_direction_count": len(directions),
        "sampled_peak_direction_examples_theta_phi_deg": directions[:8],
        "peak_elevation_range_deg": [min(90 - t for t, _ in directions), max(90 - t for t, _ in directions)],
        "front_to_back_db": front_back,
        "front_to_back_definition": (
            f"Gain(theta=90, phi={front_phi}) - Gain(theta=90, phi={(front_phi + 180) % 360}); fixed design axis"
            if front_phi is not None
            else "Not defined: no unique forward direction assigned to this fixture"
        ),
        "angular_step_deg": 5,
    }


def render_fields(output: Path, case: Case, fields: list[tuple[str, Field]]) -> None:
    """Draw fixed physical cuts; display floors never overwrite raw numerical data."""
    for cut in ("azimuth", "elevation"):
        angles = list(range(0, 360, 5))
        keys = (
            [(90, angle) for angle in angles]
            if cut == "azimuth"
            else [(90 - a, 90) if a <= 90 else (a - 90, 270) if a <= 270 else (450 - a, 90) for a in angles]
        )
        peaks = [max(v[2] for v in field.values()) for _, field in fields]
        figure, axes = plt.subplots(1, 2, figsize=(11, 5), subplot_kw={"projection": "polar"})
        for ax, normalized in zip(axes, (False, True), strict=True):
            for (label, field), peak in zip(fields, peaks, strict=True):
                gains = [max(-40, field[key][2] - (peak if normalized else 0)) for key in keys]
                (line,) = ax.plot(
                    [math.radians(a) for a in [*angles, 360]],
                    [*gains, gains[0]],
                    label=f"{label}: max {peak:.3f} dBi",
                )
                peak_index = max(range(len(keys)), key=lambda index: field[keys[index]][2])
                ax.plot(math.radians(angles[peak_index]), gains[peak_index], "o", color=line.get_color())
            ax.set_ylim(-40, 0 if normalized else max(5, math.ceil(max(peaks) / 5) * 5))
            ax.set_xticks(
                [0, math.pi / 2, math.pi, 1.5 * math.pi],
                ["+X", "+Y", "-X", "-Y"] if cut == "azimuth" else ["+Y", "+Z", "-Y", "-Z"],
            )
            ax.set_title("Normalized dB; full-sphere peak = 0" if normalized else "Absolute dBi")
            ax.legend(fontsize=7, loc="lower center", bbox_to_anchor=(0.5, -0.27))
        plane = "XY plane, elevation=0 deg" if cut == "azimuth" else "YZ plane, phi=90/270 deg"
        figure.suptitle(f"{case.name} | {case.frequency_mhz:g} MHz | {plane}")
        direction = "front +Y / back -Y" if case.front_phi_deg == 90 else "no unique front/back"
        figure.text(0.5, 0.02, f"Display floor -40 dB; 5-degree samples; {direction}", ha="center")
        figure.tight_layout(rect=(0, 0.12, 1, 0.94))
        for extension in ("png", "svg"):
            figure.savefig(output / f"{cut}.{extension}", dpi=140)
        plt.close(figure)


def solve(case: Case, segments: int, output: Path, *, end_taper: bool = False) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    wires = [mininec.Wire(segments, *wire) for wire in case.wires]
    taper_limits = []
    if end_taper:
        for wire, physical in zip(wires, case.wires, strict=True):
            length = math.dist(physical[:3], physical[3:6])
            # Independent geometry-based limits exercise the public taper API;
            # no GAL pulse spacing, impedance or field values enter this choice.
            wire.segtype = 3
            wire.taper_min = max(4 * physical[6], length / 512)
            wire.taper_max = length / 16
            taper_limits.append({"minimum_m": wire.taper_min, "maximum_m": wire.taper_max})
    model = mininec.Mininec(case.frequency_mhz, wires, media=None)
    if end_taper and any(wire.segtype != 3 for wire in wires):
        raise ValueError("Taper request fell back to uniform; retain failure, not mislabel the mesh")
    points = np.array([pulse.point for pulse in model.pulses])
    matches = np.flatnonzero(np.all(np.isclose(points, case.source_position_m, rtol=0, atol=1e-12), axis=1))
    if len(matches) != 1:
        raise ValueError(f"{case.name}: physical feed not represented by exactly one pulse")
    feed = int(matches[0])
    source = mininec.Excitation(1.0, 0.0)
    model.register_source(source, feed)
    model.compute()
    theta, phi = mininec.Angle(0, 5, 37), mininec.Angle(0, 5, 72)
    model.compute_far_field(theta, phi)
    z = complex(source.impedance)
    reflection = abs((z - 50) / (z + 50))
    if not (math.isfinite(z.real) and math.isfinite(z.imag) and 0 <= reflection < 1):
        raise ValueError("Non-passive or non-finite impedance")
    raw = model.far_field
    vertical, horizontal, total = raw.gain.T
    field = {
        (float(t), float(p)): [float(v), float(h), float(g)]
        for t, p, v, h, g in zip(raw.zen.flat, raw.azi.flat, vertical.flat, horizontal.flat, total.flat, strict=True)
    }
    summary = field_summary(field, case.front_phi_deg)
    write_json(
        output / "input.json",
        {
            **asdict(case),
            "segments_per_wire": segments,
            "candidate_segmentation": "native both-end taper" if end_taper else "native uniform",
            "taper_limits_by_wire": taper_limits,
            "source_voltage_v": 1,
            "reference_impedance_ohm": 50,
            "material": "perfect conductor; no finite conductivity or insulation in these geometry fixtures",
        },
    )
    (output / "reference-input.maa").write_bytes(case.maa(case.frequency_mhz).encode("ascii"))
    write_json(
        output / "currents.json",
        {
            "feed_index_zero_based": feed,
            "samples": [
                {
                    "position_m": pulse.point.tolist(),
                    "adjacent_wires_one_based": sorted({int(wire.n) + 1 for wire in pulse.geo}),
                    "real_a": float(current.real),
                    "imag_a": float(current.imag),
                }
                for pulse, current in zip(model.pulses, model.current, strict=True)
            ],
            "segments_by_wire": [
                [{"start_m": segment.p1.tolist(), "end_m": segment.p2.tolist()} for segment in wire.segments]
                for wire in wires
            ],
        },
    )
    write_json(
        output / "far_field.json",
        {
            "frequency_hz": round(case.frequency_mhz * 1e6),
            "coordinate_system": "theta from +Z; phi from +X toward +Y; degrees",
            "angular_domain": "full sphere",
            "gain_unit": "dBi",
            "polarization": "pymininec V=theta, H=phi",
            "solver_floor_marker": FLOOR,
            "normalized_reference_peak_dbi": summary["sampled_peak_gain_dbi"],
            "samples": [
                {
                    "theta_deg": t,
                    "phi_deg": p,
                    "vertical_gain_dbi": v,
                    "horizontal_gain_dbi": h,
                    "total_gain_dbi": g,
                    "normalized_gain_db": None if g == FLOOR else g - summary["sampled_peak_gain_dbi"],
                }
                for (t, p), (v, h, g) in field.items()
            ],
        },
    )
    (output / "pymininec.out").write_text(model.as_mininec(options={"far-field"}) + "\n", encoding="utf-8")
    (output / "pymininec.args").write_text(model.as_cmdline(azi=phi, zen=theta), encoding="utf-8")
    result = {
        "case": case.name,
        "frequency_mhz": case.frequency_mhz,
        "segments_per_wire": segments,
        "segmentation": "native both-end taper" if end_taper else "native uniform",
        "pulse_count": len(points),
        "impedance_ohm": {"r": z.real, "x": z.imag},
        "swr_z0_50": (1 + reflection) / (1 - reflection),
        "feed_current_a": {"real": float(model.current[feed].real), "imag": float(model.current[feed].imag)},
        **summary,
    }
    write_json(output / "result.json", result)
    if segments == MESHES[-1]:
        render_fields(output, case, [(f"pymininec N{segments}/wire", field)])
    return result


def run(output: Path) -> dict:
    if metadata.version("pymininec") != "1.2.0" or digest(Path(mininec.__file__)) != SOLVER_SHA256:
        raise ValueError("Use the reviewed, unchanged pymininec 1.2.0 source")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    summaries = []
    for case in GEOMETRY_CASES:
        results = [solve(case, segments, output / case.name / f"N{segments}") for segments in MESHES]
        previous, finest = results[-2:]
        summary = {
            "case": case.name,
            "meshes": results,
            "finest_minus_previous": {
                "r_ohm": finest["impedance_ohm"]["r"] - previous["impedance_ohm"]["r"],
                "x_ohm": finest["impedance_ohm"]["x"] - previous["impedance_ohm"]["x"],
                "peak_gain_db": finest["sampled_peak_gain_dbi"] - previous["sampled_peak_gain_dbi"],
            },
            "gal_comparison": "AWAITING_REFERENCE",
        }
        write_json(output / case.name / "summary.json", summary)
        summaries.append(summary)
        print(f"{case.name}: 3 meshes solved, GAL reference pending", flush=True)
    report = {"solver_runs": 9, "llm_calls_inside_probe": 0, "elapsed_seconds": time.monotonic() - started}
    write_json(output / "summary.json", {**report, "cases": summaries})
    write_json(
        output / "compact.json",
        {
            **report,
            "cases": [
                {
                    **row["meshes"][-1],
                    "finest_minus_previous": row["finest_minus_previous"],
                    "gal_comparison": row["gal_comparison"],
                    "artifacts": {
                        "far_field": f"{row['case']}/N80/far_field.json",
                        "azimuth": f"{row['case']}/N80/azimuth.png",
                        "elevation": f"{row['case']}/N80/elevation.png",
                    },
                }
                for row in summaries
            ],
            "warnings": [
                "Numerical fixtures, not optimized construction designs",
                "Free space and ideal wires isolate connectivity; real materials/installation remain separate",
                "Fixed 5-degree sampling and finite mesh changes are not proof of convergence",
                "GAL source expressions require confirmation against exported physical feed currents",
            ],
        },
    )
    write_json(
        output / "environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "solver_source_sha256": SOLVER_SHA256,
            "dependencies": {item.metadata["Name"]: item.version for item in metadata.distributions()},
        },
    )
    for name in ("geometry_probe.py", "probe.py", "compare.py", "engineering_probe.py", "windows/recipes.py"):
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(__file__).parent / name, target)
    write_json(
        output / "manifest.json",
        {path.relative_to(output).as_posix(): digest(path) for path in sorted(output.rglob("*")) if path.is_file()},
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().output), indent=2))
