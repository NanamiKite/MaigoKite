"""Review the bounded geometry/sweep batch without rerunning either solver.

Verified base captures are reused. Missing/failed captures stay explicit; no
synthetic reference or post-hoc acceptance tolerance is used to claim a pass.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
from itertools import pairwise
from pathlib import Path

import numpy as np
from batch_compare import CURRENT_HEADER, half_step
from compare import CHANNELS, FLOOR, errors, pattern_statistics, plt, read_csv
from geometry_probe import Field, field_summary, render_fields
from probe import digest, write_json
from review_engineering import CASES as ENGINEERING_CASES
from review_engineering import reference_field, verify_manifest
from windows.recipes import CASES, GEOMETRY_CASES, NEXT_SWEEP_MHZ, Case, check_saved_model

GAL_SHA256 = "c7c5d5c2f0c85a120240ea07ddb1a0abff7e0edbf7182ca9df70813561a77cdf"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def index_references(roots: list[Path]) -> dict[tuple[str, float], Path]:
    records: dict[tuple[str, float], Path] = {}
    for root in roots:
        verify_manifest(root)
        runs = [root / "run.json"] if (root / "run.json").is_file() else list((root / "provenance").glob("*/run.json"))
        if not runs:
            raise ValueError(f"No executable provenance: {root}")
        for run in runs:
            identity = load(run)
            if identity["actual_exe_version"] != "1.2.0.20" or identity["exe_sha256"] != GAL_SHA256:
                raise ValueError(f"Different reference executable; review separately: {run}")
        for directory in sorted((root / "cases").iterdir()):
            if not directory.is_dir():
                continue
            record = load(directory / "capture.json")
            if record["status"] != "CAPTURED_REVIEW_REQUIRED":
                continue
            key = record["case"], float(record["frequency_mhz"])
            if key in records:
                raise ValueError(f"Ambiguous duplicate reference {key}; choose one original capture")
            records[key] = directory
    return records


def reference_result(directory: Path, case: Case, frequency: float) -> dict:
    check_saved_model(directory / "model.maa", case, frequency)
    if case.ground == 2:
        soil = load(directory / "capture.json")["settings"]["soil"]
        expected = {
            "epsilon_r": 13,
            "conductivity_ms_per_m": 5,
            "x_m": 0,
            "height_m": 0,
            "media_count": 1,
            "ground_screen_enabled": False,
        }
        if any(soil.get(key) != value for key, value in expected.items()):
            raise ValueError("Soil is global GUI state; saved MAA alone does not establish these inputs")
    text = (directory / "calculation.txt").read_text(encoding="utf-8")
    if "无计算错误" not in text:
        raise ValueError("Reference calculation did not report success")
    rows = [line.split() for line in text.splitlines() if line.split() and line.split()[0] == case.source]
    if len(rows) != 1 or len(rows[0]) != 5 or rows[0][1] != "1.00+j0.00":
        raise ValueError("Ambiguous source result or unexpected excitation")
    match = re.fullmatch(r"([0-9.]+)([+-])j([0-9.]+)", rows[0][3])
    if match is None:
        raise ValueError("Unrecognized impedance log")
    pulses = re.search(r"脉冲总计\s*=\s*(\d+)", text)
    if pulses is None:
        raise ValueError("Missing reference pulse count")
    return {
        "frequency_mhz": frequency,
        "pulse_count": int(pulses[1]),
        "impedance_ohm": {"r": float(match[1]), "x": float(match[3]) * (1 if match[2] == "+" else -1)},
        "swr_z0_50": float(rows[0][4]),
        "basis": "GAL log; rounded 0.01 ohm/SWR, not inferred from rounded current",
    }


def candidate_field(directory: Path) -> Field:
    samples = load(directory / "far_field.json")["samples"]
    field = {(r["theta_deg"], r["phi_deg"]): [r[channel] for channel in CHANNELS] for r in samples}
    if len(field) != len(samples):
        raise ValueError("Duplicate candidate angle samples")
    return field


def compare_currents(directory: Path, candidate: Path, case: Case, output: Path) -> dict:
    """Compare native phasors on frozen oriented wires, without sign or phase fitting.

    These fixtures use end-to-start connections or isolated parallel wires. The
    result does not validate arbitrary reordered, reversed or branching wires.
    Interpolation stays inside each wire's sampled support; absent rows stay absent.
    """
    rows = read_csv(directory / "current.csv", CURRENT_HEADER)
    samples = load(candidate / "currents.json")["samples"]
    records, differences, complex_errors, phases, skipped = [], [], [], [], []
    by_wire = {index: [] for index in range(1, len(case.wires) + 1)}
    for row in rows:
        wire_index = int(row[0])
        if float(row[0]) != wire_index or not 1 <= wire_index <= len(case.wires):
            raise ValueError("Unknown reference wire identity")
        wire = case.wires[wire_index - 1]
        start, end = np.array(wire[:3]), np.array(wire[3:6])
        axis = (end - start) / np.linalg.norm(end - start)
        point = np.array([float(v) for v in row[2:5]])
        distance = float(np.dot(point - start, axis))
        residual = float(np.linalg.norm(point - start - distance * axis))
        if residual > math.sqrt(sum(half_step(v) ** 2 for v in row[2:5])) + 1e-9:
            raise ValueError("Reference current position is off its physical wire")
        support = sorted(
            (float(np.dot(np.array(s["position_m"]) - start, axis)), complex(s["real_a"], s["imag_a"]))
            for s in samples
            if wire_index in s["adjacent_wires_one_based"]
        )
        positions = [v[0] for v in support]
        if len(positions) < 2 or any(a >= b for a, b in pairwise(positions)):
            raise ValueError("Candidate per-wire support is not uniquely ordered")
        if not positions[0] <= distance <= positions[-1]:
            skipped.append({"wire": wire_index, "pulse": row[1]})
            continue
        current = complex(np.interp(distance, positions, [v[1] for v in support]))
        # Retain the original magnitude-only interpolation as a separate metric;
        # abs(interpolate(complex I)) is not interpolate(abs(I)).
        magnitude = float(np.interp(distance, positions, [abs(v[1]) for v in support]))
        reference = complex(float(row[5]), float(row[6]))
        differences.append(abs(reference) - magnitude)
        delta = current - reference
        complex_errors.append(abs(delta))
        rounding_radius = math.hypot(half_step(row[5]), half_step(row[6]))
        phase = None
        if abs(reference) > rounding_radius and current != 0:
            relative = current * reference.conjugate()
            phase = math.degrees(math.atan2(relative.imag, relative.real))
            phases.append(phase)
        records.append(
            [
                wire_index,
                row[1],
                distance,
                reference.real,
                reference.imag,
                current.real,
                current.imag,
                delta.real,
                delta.imag,
                phase,
            ]
        )
        by_wire[wire_index].append((distance, reference, current))
    if not differences:
        raise ValueError("No common current domain")
    with (output / f"currents-{candidate.name}.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "wire",
                "pulse",
                "arclength_m",
                "reference_real_a",
                "reference_imag_a",
                "interpolated_real_a",
                "interpolated_imag_a",
                "delta_real_a",
                "delta_imag_a",
                "phase_delta_deg",
            ]
        )
        writer.writerows(records)
    figure, axes = plt.subplots(len(case.wires), 2, figsize=(11, 2.6 * len(case.wires)), squeeze=False)
    for wire_index, values in by_wire.items():
        for column, component in enumerate(("real", "imag")):
            axis = axes[wire_index - 1, column]
            axis.plot([v[0] for v in values], [getattr(v[1], component) * 1000 for v in values], ".", label="GAL")
            axis.plot(
                [v[0] for v in values],
                [getattr(v[2], component) * 1000 for v in values],
                "-",
                label=f"{candidate.name}: interpolated",
            )
            axis.set(xlabel=f"Wire {wire_index} distance from start (m)", ylabel=f"{component} I (mA)")
            axis.grid(True)
    axes[0, 0].legend(fontsize=8)
    figure.suptitle(f"{case.name} | {case.frequency_mhz:g} MHz | native signed current, 1 V / 0 deg")
    figure.tight_layout()
    for extension in ("png", "svg"):
        figure.savefig(output / f"currents-{candidate.name}.{extension}", dpi=140)
    plt.close(figure)
    return {
        "magnitude_error_a": errors(differences),
        "complex_error_a": errors(complex_errors),
        "wrapped_phase_error_deg": errors(phases) if phases else None,
        "phase_count": len(phases),
        "reference_export_rows": len(rows),
        "reference_export_pulse_ids": sorted({int(row[1]) for row in rows}),
        "outside_common_support": skipped,
        "policy": "Per-wire linear interpolation; candidate minus reference, no extrapolation/sign/scaling/phase fit",
        "magnitude_metric": "Original interpolate(abs I) comparison retained separately from complex interpolation",
        "phase_orientation": "Native phasors on frozen wire order; end-to-start/isolated-wire fixtures only",
        "phase_limits": "Exclude zero-containing reference rounding cells and zero candidate; includes rounding error",
    }


def field_differences(field: Field, other: Field) -> dict:
    """Retain dB differences and expose deep-null sensitivity without a pass filter."""
    if field.keys() != other.keys():
        raise ValueError("Angular grids differ; no implicit rotation or interpolation allowed")
    keys = sorted(field)
    stats = {}
    for index, channel in enumerate(CHANNELS):
        a, b = [field[k][index] for k in keys], [other[k][index] for k in keys]
        if not any(x != FLOOR and y != FLOOR for x, y in zip(a, b, strict=True)):
            stats[channel] = {"finite_pair_count": 0, "status": "NO_COMMON_FINITE_SAMPLES"}
        else:
            stats[channel] = pattern_statistics(keys, a, b)
    # A tiny displacement of a null can have a large dB error but little radiated
    # power. Use a common reference peak, so gain offsets are not normalized away.
    peak = max(value[2] for value in field.values())
    unique = [key for key in keys if key[0] not in (0, 180) or key[1] == 0]
    linear = [
        (0 if other[key][2] == FLOOR else 10 ** ((other[key][2] - peak) / 10))
        - (0 if field[key][2] == FLOOR else 10 ** ((field[key][2] - peak) / 10))
        for key in unique
    ]
    largest = max(keys, key=lambda k: abs(field[k][2] - other[k][2]) if FLOOR not in (field[k][2], other[k][2]) else -1)
    bands = {}
    for depth in (20, 40):
        delta = [
            other[k][2] - field[k][2]
            for k in unique
            if FLOOR not in (field[k][2], other[k][2]) and max(field[k][2], other[k][2]) >= peak - depth
        ]
        bands[f"either_above_reference_peak_minus_{depth}_db"] = errors(delta) if delta else None
    stats["total_power_diagnostic"] = {
        "linear_power_error_relative_to_reference_peak": errors(linear),
        "definition": "10^((Gcandidate-Gref_peak)/10) - 10^((Gref-Gref_peak)/10)",
        "sampling": "Unweighted unique directions; each physical pole once, not a solid-angle integral",
        "floor_policy": "-999 interpreted as zero power only here; raw floor mismatch counts remain above",
        "db_bands": bands,
        "largest_finite_db_difference": {
            "theta_phi_deg": largest,
            "reference_dbi": field[largest][2],
            "candidate_dbi": other[largest][2],
        },
        "acceptance": "Diagnostic only; no band masking is used to declare compatibility",
    }
    return stats


def compare_solution(case: Case, reference: Path, candidate: Path, output: Path) -> dict:
    result = reference_result(reference, case, case.frequency_mhz)
    field, other = reference_field(reference, False), candidate_field(candidate)
    actual = load(candidate / "result.json")
    currents = compare_currents(reference, candidate, case, output)
    exported = currents.pop("reference_export_pulse_ids")
    currents["reference_log_pulse_count"] = result["pulse_count"]
    currents["pulse_ids_absent_from_export"] = sorted(set(range(1, result["pulse_count"] + 1)) - set(exported))
    return {
        "segments_per_wire": actual["segments_per_wire"],
        "candidate": actual,
        "impedance_delta_ohm_candidate_minus_reference": {
            k: actual["impedance_ohm"][k] - result["impedance_ohm"][k] for k in ("r", "x")
        },
        "pattern": field_differences(field, other),
        "currents": currents,
        "candidate_pattern": field_summary(other, case.front_phi_deg),
    }


def compare_geometry(case: Case, reference: Path, candidates: Path, output: Path) -> dict:
    result = reference_result(reference, case, case.frequency_mhz)
    field = reference_field(reference, False)
    report = {"case": case.name, "reference": {**result, **field_summary(field, case.front_phi_deg)}, "meshes": []}
    for segments in (20, 40, 80):
        candidate = candidates / case.name / f"N{segments}"
        other = candidate_field(candidate)
        report["meshes"].append(compare_solution(case, reference, candidate, output))
        if segments == 80:
            render_fields(output, case, [("GAL 1.2.0.20", field), ("pymininec N80/wire", other)])
    write_json(output / "comparison.json", report)
    return report


def sweep_report(case: Case, refs: dict, candidates: Path, output: Path) -> dict:
    condition = ENGINEERING_CASES[case.name][0]
    rows = []
    for frequency in sorted((case.frequency_mhz, *NEXT_SWEEP_MHZ)):
        candidate = candidates / condition / ("N80" if frequency == 7.1 else f"sweep-{frequency:.2f}")
        actual = load(candidate / "result.json")
        reference = refs.get((case.name, frequency))
        row = {"frequency_mhz": frequency, "candidate": actual, "reference": None}
        if reference:
            result = reference_result(reference, case, frequency)
            row["reference"] = result
            row["delta_candidate_minus_reference"] = {
                k: actual["impedance_ohm"][k] - result["impedance_ohm"][k] for k in ("r", "x")
            }
        rows.append(row)
    brackets = {}
    for source in ("candidate", "reference"):
        brackets[source] = [
            [a["frequency_mhz"], b["frequency_mhz"]]
            for a, b in pairwise(rows)
            if a[source] is not None
            and b[source] is not None
            and a[source]["impedance_ohm"]["x"] * b[source]["impedance_ohm"]["x"] <= 0
        ]
    figure, axes = plt.subplots(1, 3, figsize=(13, 4))
    for source in ("candidate", "reference"):
        present = [row for row in rows if row[source] is not None]
        for axis, key in zip(axes, ("r", "x", "swr"), strict=True):
            axis.plot(
                [row["frequency_mhz"] for row in rows],
                [
                    math.nan
                    if row[source] is None
                    else row[source]["swr_z0_50"]
                    if key == "swr"
                    else row[source]["impedance_ohm"][key]
                    for row in rows
                ],
                marker="o",
                label=f"{source} ({len(present)}/5)",
            )
            axis.set(xlabel="MHz", ylabel="SWR 50 ohm" if key == "swr" else f"{key.upper()} (ohm)")
            axis.grid(True)
    axes[0].legend()
    figure.suptitle(f"{case.name}: fixed geometry, native meshes; missing reference points are gaps")
    figure.tight_layout()
    for extension in ("png", "svg"):
        figure.savefig(output / f"{case.name}-sweep.{extension}", dpi=140)
    plt.close(figure)
    return {
        "case": case.name,
        "points": rows,
        "zero_reactance_brackets_mhz": brackets,
        "reference_count": sum(row["reference"] is not None for row in rows),
        "limits": "Five points establish coarse trends only, not exact resonance or SWR bandwidth",
        "material_model_warning": "GAL copper and distributed conductivity are not equivalent" if case.copper else None,
    }


def run(reference_roots: list[Path], engineering: Path, geometry: Path, output: Path) -> dict:
    refs = index_references(reference_roots)
    for directory in (engineering, geometry):
        verify_manifest(directory)
    output.mkdir(parents=True, exist_ok=False)
    missing, comparisons, compact = [], [], []
    for case in GEOMETRY_CASES:
        destination = output / case.name
        destination.mkdir()
        candidate = geometry / case.name / "N80"
        field = candidate_field(candidate)
        actual = load(candidate / "result.json")
        compact.append(
            {
                "case": case.name,
                "frequency_mhz": case.frequency_mhz,
                "impedance_ohm": actual["impedance_ohm"],
                "swr_z0_50": actual["swr_z0_50"],
                **field_summary(field, case.front_phi_deg),
                "finest_minus_previous": load(geometry / case.name / "summary.json")["finest_minus_previous"],
                "reference_available": (case.name, case.frequency_mhz) in refs,
                "artifacts": {"raw_candidate": str(candidate.resolve()), "plots": case.name},
            }
        )
        reference = refs.get((case.name, case.frequency_mhz))
        if reference:
            comparison = compare_geometry(case, reference, geometry, destination)
            comparisons.append(comparison)
            finest = comparison["meshes"][-1]
            compact[-1]["reference"] = comparison["reference"]
            compact[-1]["n80_delta_ohm"] = finest["impedance_delta_ohm_candidate_minus_reference"]
            compact[-1]["n80_total_pattern_db"] = finest["pattern"]["total_gain_dbi"]["absolute_db"]
            compact[-1]["n80_linear_power_diagnostic"] = finest["pattern"]["total_power_diagnostic"][
                "linear_power_error_relative_to_reference_peak"
            ]
            compact[-1]["current_export_missing_pulse_ids"] = finest["currents"]["pulse_ids_absent_from_export"]
        else:
            missing.append(case.name)
            render_fields(destination, case, [("pymininec N80/wire; GAL pending", field)])
    sweeps = []
    for case in CASES:
        if case.refined:
            continue
        report = sweep_report(case, refs, engineering, output)
        sweeps.append(report)
        missing.extend(
            f"{case.name}-{row['frequency_mhz']:.2f}MHz" for row in report["points"] if row["reference"] is None
        )
    summary = {
        "status": "AWAITING_REFERENCE" if missing else "DIFFERENCES_RECORDED",
        "compatibility_pass": None,
        "missing_reference": missing,
        "solver_runs": 0,
        "llm_calls_inside_program": 0,
        "geometry": comparisons,
        "sweeps": sweeps,
        "provenance": [
            {"root": str(root.resolve()), "manifest_sha256": digest(root / "manifest.json")}
            for root in (*reference_roots, engineering, geometry)
        ],
    }
    write_json(output / "summary.json", summary)
    write_json(
        output / "compact.json",
        {
            "status": summary["status"],
            "missing_reference_count": len(missing),
            "compatibility_pass": None,
            "geometry": compact,
            "sweeps": [{k: v for k, v in row.items() if k != "points"} for row in sweeps],
            "solver_runs": 0,
        },
    )
    for name in (
        "review_next.py",
        "geometry_probe.py",
        "probe.py",
        "engineering_probe.py",
        "review_engineering.py",
        "review_semantics.py",
        "batch_compare.py",
        "compare.py",
        "windows/recipes.py",
    ):
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(__file__).parent / name, target)
    write_json(
        output / "manifest.json",
        {path.relative_to(output).as_posix(): digest(path) for path in sorted(output.rglob("*")) if path.is_file()},
    )
    return {"status": summary["status"], "missing_reference_count": len(missing), "solver_runs": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, nargs="+", required=True)
    parser.add_argument("--engineering", type=Path, required=True)
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.references, args.engineering, args.geometry, args.output), indent=2))
