"""Measure material, load and ground effects with independent native meshes.

Five fixed conditions share one HF dipole. Public pymininec APIs perform all
calculations; raw fields and currents stay in artifacts rather than LLM input.
These candidate results require separate GAL reference captures.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import shutil
import time
from dataclasses import asdict, dataclass
from importlib import metadata
from itertools import pairwise
from pathlib import Path

import matplotlib
import numpy as np
from mininec import mininec
from probe import digest, write_json

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

FREQUENCY_MHZ = 7.1
SWEEP_MHZ = tuple(round(6.8 + index * 0.05, 2) for index in range(13))
MESHES = (40, 80, 160)
SWEEP_MESH = 80
MAX_SOLVER_RUNS = 75
SOLVER_SHA256 = "ddcc416b292d6eae28c5cf04209d67f02e73f04d3655ce7b3b757377cfe015e2"


@dataclass(frozen=True)
class Condition:
    name: str
    conductivity_s_per_m: float | None = None
    feed_resistance_ohm: float = 0.0
    ground: str = "free_space"


CONDITIONS = (
    Condition("free-lossless"),
    Condition("free-finite-conductivity", conductivity_s_per_m=5.8e7),
    Condition("free-feed-resistor", feed_resistance_ohm=10.0),
    Condition("ideal-ground", ground="ideal"),
    Condition("lossy-ground", ground="lossy"),
)


def solve(condition: Condition, frequency: float, segments: int, output: Path, *, pattern: bool) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    wire = mininec.Wire(segments, -10.0, 0.0, 5.0, 10.0, 0.0, 5.0, 0.0005)
    media = None
    if condition.ground == "ideal":
        media = [mininec.Medium(0, 0)]
    elif condition.ground == "lossy":
        media = [mininec.Medium(13, 0.005)]
    model = mininec.Mininec(frequency, [wire], media=media)
    points = np.asarray([pulse.point for pulse in model.pulses])
    matches = np.flatnonzero(np.all(np.isclose(points, (0, 0, 5), rtol=0, atol=1e-12), axis=1))
    if len(matches) != 1:
        raise ValueError("The native mesh must preserve the physical centre feed.")
    feed = int(matches[0])
    source = mininec.Excitation(1.0, 0.0)
    model.register_source(source, feed)
    if condition.conductivity_s_per_m is not None:
        model.register_load(mininec.Skin_Effect_Load(wire, conductivity=condition.conductivity_s_per_m))
    if condition.feed_resistance_ohm:
        model.register_load(mininec.Impedance_Load(complex(condition.feed_resistance_ohm)), feed)
    model.compute()
    z = complex(source.impedance)
    reflection = abs((z - 50) / (z + 50))
    if not (math.isfinite(z.real) and math.isfinite(z.imag) and 0 <= reflection < 1):
        raise ValueError("Invalid passive-antenna impedance; retain the failed run separately.")
    result = {
        "condition": condition.name,
        "frequency_mhz": frequency,
        "segments": segments,
        "impedance_ohm": {"r": z.real, "x": z.imag},
        "swr_z0_50": (1 + reflection) / (1 - reflection),
        "feed_current_a": {"real": float(model.current[feed].real), "imag": float(model.current[feed].imag)},
        "sampled_peak_gain_dbi": None,
        "sampled_peak_directions_deg": [],
    }
    write_json(output / "input.json", {**asdict(condition), "frequency_mhz": frequency, "segments": segments})
    write_json(
        output / "currents.json",
        {
            "feed_index_zero_based": feed,
            "segments": [{"start_m": s.p1.tolist(), "end_m": s.p2.tolist()} for s in wire.segments],
            "samples": [
                {"position_m": point.tolist(), "real_a": float(current.real), "imag_a": float(current.imag)}
                for point, current in zip(points, model.current, strict=True)
            ],
        },
    )
    theta = mininec.Angle(0, 5, 37 if media is None else 19)
    phi = mininec.Angle(0, 5, 72)
    if pattern:
        model.compute_far_field(theta, phi)
        field = model.far_field
        vertical, horizontal, total = field.gain.T
        peak = float(np.max(total))
        samples = [
            {
                "theta_deg": float(t),
                "phi_deg": float(p),
                "total_gain_dbi": float(g),
                "vertical_gain_dbi": float(v),
                "horizontal_gain_dbi": float(h),
                "normalized_gain_db": float(g - peak) if g != -999 else None,
            }
            for t, p, g, v, h in zip(
                field.zen.flat, field.azi.flat, total.flat, vertical.flat, horizontal.flat, strict=True
            )
        ]
        result["sampled_peak_gain_dbi"] = peak
        result["sampled_peak_directions_deg"] = [
            {"theta": row["theta_deg"], "phi": row["phi_deg"]}
            for row in samples
            if abs(row["total_gain_dbi"] - peak) < 1e-9
        ]
        write_json(
            output / "far_field.json",
            {
                "frequency_hz": round(frequency * 1e6),
                "coordinate_system": "theta from +Z; phi from +X toward +Y; degrees",
                "gain_unit": "dBi",
                "polarization": "pymininec V=theta, H=phi",
                "solver_floor_marker": -999,
                "normalized_reference_peak_dbi": peak,
                "angular_domain": "full sphere" if media is None else "above ground only",
                "samples": samples,
            },
        )
    (output / "pymininec.out").write_text(
        model.as_mininec(options={"far-field"} if pattern else set()) + "\n", encoding="utf-8"
    )
    # The solver exports its own CLI parameters, including physical loads.
    (output / "pymininec.args").write_text(
        model.as_cmdline(azi=phi if pattern else None, zen=theta if pattern else None), encoding="utf-8"
    )
    write_json(output / "result.json", result)
    return result


def render_pattern(directory: Path) -> None:
    raw = json.loads((directory / "far_field.json").read_text(encoding="utf-8"))
    values = {(r["theta_deg"], r["phi_deg"]): r["total_gain_dbi"] for r in raw["samples"]}
    peak = raw["normalized_reference_peak_dbi"]
    above_ground = raw["angular_domain"] == "above ground only"
    cuts = (
        (
            "azimuth",
            [(75, phi) for phi in range(0, 360, 5)],
            list(range(0, 360, 5)),
            "elevation=15 degrees",
            ["+X", "+Y", "-X", "-Y"],
        ),
        (
            "elevation",
            [
                (90 - a, 90) if a <= 90 else (a - 90, 270) if a <= 270 else (450 - a, 90)
                for a in range(0, 181 if above_ground else 360, 5)
            ],
            list(range(0, 181 if above_ground else 360, 5)),
            "YZ plane; phi=90/270",
            ["+Y", "+Z", "-Y", "-Z"],
        ),
    )
    for name, keys, angles, plane, labels in cuts:
        figure, axes = plt.subplots(1, 2, figsize=(11, 5), subplot_kw={"projection": "polar"})
        for axis, normalized in zip(axes, (False, True), strict=True):
            gains = [max(-40, values[key] - (peak if normalized else 0)) for key in keys]
            radians = [math.radians(angle) for angle in angles]
            if len(angles) == 72:
                radians += [2 * math.pi]
                gains += gains[:1]
            axis.plot(radians, gains)
            axis.set_ylim(-40, 0 if normalized else max(5, math.ceil(peak / 5) * 5))
            axis.set_xticks([0, math.pi / 2, math.pi, 1.5 * math.pi], labels)
            if name == "elevation" and above_ground:
                axis.set_thetamax(180)
            axis.set_title("Normalized dB; sampled peak = 0" if normalized else "Absolute dBi")
        figure.suptitle(f"{directory.parent.name} | {FREQUENCY_MHZ} MHz | {plane}\nSampled peak {peak:.3f} dBi")
        figure.text(0.5, 0.02, "Display floor -40 dB; symmetric dipole, no unique front/back direction", ha="center")
        figure.tight_layout(rect=(0, 0.04, 1, 0.88))
        for extension in ("png", "svg"):
            figure.savefig(directory / f"{name}.{extension}", dpi=140)
        plt.close(figure)


def compact_report(report: dict) -> dict:
    """Project engineering decisions without returning field arrays or logs."""
    conditions = []
    for condition in report["conditions"]:
        nominal = next(row for row in condition["mesh_results"] if row["segments"] == SWEEP_MESH)
        sweep = condition["sweep_results"]
        crossings = [
            [left["frequency_mhz"], right["frequency_mhz"]]
            for left, right in pairwise(sweep)
            if left["impedance_ohm"]["x"] * right["impedance_ohm"]["x"] <= 0
        ]
        elevations = [90 - point["theta"] for point in nominal["sampled_peak_directions_deg"]]
        conditions.append(
            {
                "condition": condition["condition"],
                "frequency_mhz": nominal["frequency_mhz"],
                "impedance_ohm": nominal["impedance_ohm"],
                "swr_z0_50": nominal["swr_z0_50"],
                "sampled_peak_gain_dbi": nominal["sampled_peak_gain_dbi"],
                "peak_elevation_range_deg": [min(elevations), max(elevations)],
                "front_to_back_db": None,
                "front_to_back_reason": "Symmetric dipole; no unique forward direction defined",
                "zero_reactance_brackets_mhz": crossings,
                "mesh_change_80_to_160": condition["finest_minus_previous"],
                "gal_comparison": condition["gal_comparison"],
                "artifacts": {
                    "full_summary": f"{condition['condition']}/summary.json",
                    "far_field": f"{condition['condition']}/N80/far_field.json",
                    "azimuth": f"{condition['condition']}/N80/azimuth.png",
                    "elevation": f"{condition['condition']}/N80/elevation.png",
                },
            }
        )
    return {
        "solver_runs": report["solver_runs"],
        "llm_calls_inside_probe": 0,
        "conditions": conditions,
        "warnings": [
            "Candidate calculations, not GAL reference or a verified construction design",
            "Conductivity and soil parameters are explicit assumptions, not measured site/material properties",
            "5 m at 7.1 MHz is below 0.2 wavelengths; MININEC ground impedance approximation is limited",
            "Native mesh deltas are recorded at 7.1 MHz only; sweep uses N80, not convergence at every frequency",
            "50 kHz frequency and 5-degree angular sampling do not give exact resonance or beamwidth",
        ],
    }


def run(output: Path) -> dict:
    if metadata.version("pymininec") != "1.2.0" or digest(Path(mininec.__file__)) != SOLVER_SHA256:
        raise ValueError("Use the reviewed pymininec 1.2.0 source, unchanged.")
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    config = {
        "wire_start_m": [-10, 0, 5],
        "wire_end_m": [10, 0, 5],
        "radius_m": 0.0005,
        "source_position_m": [0, 0, 5],
        "source_voltage_v": 1,
        "source_phase_deg": 0,
        "reference_impedance_ohm": 50,
        "mesh": "native uniform; independently chosen, not GAL-derived",
        "meshes": MESHES,
        "sweep_mesh": SWEEP_MESH,
        "sweep_mhz": SWEEP_MHZ,
        "pattern_frequency_mhz": FREQUENCY_MHZ,
        "max_solver_runs": MAX_SOLVER_RUNS,
        "lossy_ground": {"relative_permittivity": 13, "conductivity_s_per_m": 0.005, "height_m": 0},
        "material_basis": "Explicit illustrative conductivity, not measured user material or a GAL preset",
        "ground_basis": "Illustrative homogeneous soil, not a measured site; native MININEC ground approximation",
        "excluded": ["insulation", "connectors", "feedline", "matching network", "power handling", "thermal effects"],
        "conditions": [asdict(condition) for condition in CONDITIONS],
    }
    write_json(output / "experiment.json", config)
    reports = []
    solver_runs = 0
    for condition in CONDITIONS:
        grids = []
        for segments in MESHES:
            if solver_runs >= MAX_SOLVER_RUNS:
                raise ValueError("Fixed numerical budget exhausted")
            result = solve(condition, FREQUENCY_MHZ, segments, output / condition.name / f"N{segments}", pattern=True)
            solver_runs += 1
            grids.append(result)
        sweep = []
        for frequency in SWEEP_MHZ:
            if frequency == FREQUENCY_MHZ:
                sweep.append(grids[MESHES.index(SWEEP_MESH)])
                continue
            if solver_runs >= MAX_SOLVER_RUNS:
                raise ValueError("Fixed numerical budget exhausted")
            sweep.append(
                solve(
                    condition, frequency, SWEEP_MESH, output / condition.name / f"sweep-{frequency:.2f}", pattern=False
                )
            )
            solver_runs += 1
        render_pattern(output / condition.name / f"N{SWEEP_MESH}")
        previous, finest = grids[-2:]
        delta = {key: finest["impedance_ohm"][key] - previous["impedance_ohm"][key] for key in ("r", "x")}
        report = {
            "condition": condition.name,
            "mesh_results": grids,
            "sweep_results": sweep,
            "finest_minus_previous": {
                "impedance_ohm": delta,
                "peak_gain_db": finest["sampled_peak_gain_dbi"] - previous["sampled_peak_gain_dbi"],
            },
            "lowest_sampled_swr": min(sweep, key=lambda row: row["swr_z0_50"]),
            "swr_at_most_2_sampled_frequencies_mhz": [row["frequency_mhz"] for row in sweep if row["swr_z0_50"] <= 2],
            "convergence_status": "DELTAS_RECORDED",
            "gal_comparison": "AWAITING_REFERENCE",
        }
        write_json(output / condition.name / "summary.json", report)
        reports.append(report)
        print(f"Computed {condition.name}: {solver_runs}/{MAX_SOLVER_RUNS} solves", flush=True)
    figure, axes = plt.subplots(1, 3, figsize=(15, 4))
    for report in reports:
        rows = report["sweep_results"]
        for axis, key, label in zip(axes, ("r", "x", "swr"), ("R (ohm)", "X (ohm)", "SWR, Z0=50 ohm"), strict=True):
            axis.plot(
                SWEEP_MHZ,
                [r["swr_z0_50"] if key == "swr" else r["impedance_ohm"][key] for r in rows],
                marker=".",
                label=report["condition"],
            )
            axis.set(xlabel="Frequency (MHz)", ylabel=label)
            axis.grid(True)
    axes[-1].legend(fontsize=7)
    figure.suptitle("Fixed 20 m dipole; native N80 mesh fixed across frequency; candidate only")
    figure.tight_layout()
    for extension in ("png", "svg"):
        figure.savefig(output / f"sweep.{extension}", dpi=140)
    plt.close(figure)
    report = {
        "solver_runs": solver_runs,
        "llm_calls_inside_probe": 0,
        "elapsed_seconds": time.monotonic() - start,
        "conditions": reports,
    }
    write_json(output / "summary.json", report)
    write_json(output / "compact.json", compact_report(report))
    write_json(
        output / "environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "solver_source_sha256": SOLVER_SHA256,
            "dependencies": {item.metadata["Name"]: item.version for item in metadata.distributions()},
        },
    )
    shutil.copy2(__file__, output / Path(__file__).name)
    shutil.copy2(Path(__file__).with_name("probe.py"), output / "probe.py")
    write_json(
        output / "manifest.json",
        {str(path.relative_to(output)): digest(path) for path in sorted(output.rglob("*")) if path.is_file()},
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="A new directory; existing evidence is never overwritten"
    )
    args = parser.parse_args()
    report = run(args.output)
    print(json.dumps({key: value for key, value in report.items() if key != "conditions"}, indent=2))


if __name__ == "__main__":
    main()
