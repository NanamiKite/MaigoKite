"""Compare the four fixed A01 captures without executing or adjusting a solver.

Round-off intervals quantify CSV precision, not compatibility tolerances. The
result retains missing metadata and never substitutes inferred impedance for a
direct GAL result. Shared inputs and previous evidence are not modified.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import shutil
from decimal import Decimal
from pathlib import Path

import matplotlib
from compare import CHANNELS, FLOOR, digest, errors, pattern_statistics, plt, read_csv, render_cut, write_json

CASES = ("frequency-140MHz-N20", "frequency-150MHz-N20", "mesh-145MHz-N40", "mesh-145MHz-N80")
CURRENT_HEADER = ["导线", "脉冲", "X(m)", "Y(m)", "Z(m)", "实部(A)", "虚部(A)", "幅度(A)", "相位(度)"]
PATTERN_HEADER = ["仰角(度)", "方位(度)", "垂直(dBi)", "水平(dBi)", "总共(dBi)"]


def half_step(text: str) -> float:
    """Read each field's printed resolution rather than assuming one precision."""
    return float(Decimal(10) ** Decimal(text).as_tuple().exponent) / 2


def inferred_impedance(feed: list[str]) -> dict[str, object]:
    a, b = float(feed[5]), float(feed[6])
    da, db = half_step(feed[5]), half_step(feed[6])
    # Under this condition both components of 1/I are monotone on the rounding
    # rectangle, so corner extrema bound them. Refuse other cases, not guess.
    if a - da <= max(abs(b - db), abs(b + db)) or (b - db) * (b + db) <= 0:
        raise ValueError("Inverse-current interval requires a different monotonicity analysis")
    corners = [1 / complex(x, y) for x in (a - da, a + da) for y in (b - db, b + db)]
    return {
        "assumed_source_v": [1, 0],
        "feed_current_raw_a": feed[5:7],
        "r_range_ohm": [min(z.real for z in corners), max(z.real for z in corners)],
        "x_range_ohm": [min(z.imag for z in corners), max(z.imag for z in corners)],
        "meaning": "Conditional on 1 V source and nearest rounding; not direct GAL impedance or confidence interval",
    }


def compare_case(candidate: Path, reference: Path, output: Path, name: str) -> dict[str, object]:
    data = json.loads((candidate / f"{name}.json").read_text(encoding="utf-8"))
    field = json.loads((candidate / "capture-inputs" / f"{name}-far-field.json").read_text(encoding="utf-8"))
    rows = read_csv(reference / f"{name}-current.csv", CURRENT_HEADER)
    raw_pattern = read_csv(reference / f"{name}-pattern.csv", PATTERN_HEADER)
    if len(rows) != data["pulse_count"] or len(data["currents"]) != data["pulse_count"]:
        raise ValueError("Pulse count differs from the candidate")
    delta_moduli, residual_lower_bounds, signed_imaginary, coordinate_errors = [], [], [], []
    outside = [0, 0]
    with (output / "current-differences.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "pulse_1based",
                "ref_x_m",
                "ref_real_a",
                "ref_imag_a",
                "candidate_real_a",
                "candidate_imag_a",
                "delta_real_a",
                "delta_imag_a",
                "residual_lower_bound_a",
            ]
        )
        for index, (row, sample) in enumerate(zip(rows, data["currents"], strict=True)):
            if float(row[0]) != 1 or float(row[1]) != index + 1:
                raise ValueError("Wire or pulse identity mismatch")
            for text, position in zip(row[2:5], sample["position_m"], strict=True):
                distance = abs(float(text) - position)
                coordinate_errors.append(distance)
                if distance > half_step(text) + 1e-12:
                    raise ValueError("Pulse position is outside the coordinate export's rounding interval")
            ref = complex(float(row[5]), float(row[6]))
            cand = complex(sample["real_a"], sample["imag_a"])
            delta = ref - cand
            components, steps = (delta.real, delta.imag), (half_step(row[5]), half_step(row[6]))
            for component, (value, step) in enumerate(zip(components, steps, strict=True)):
                outside[component] += abs(value) > step + 1e-15
            # Distance from the candidate to the rectangular rounding cell is
            # a lower bound on unexplained error, not an estimate of true error.
            lower_bound = math.hypot(*(max(abs(v) - h, 0) for v, h in zip(components, steps, strict=True)))
            residual_lower_bounds.append(lower_bound)
            delta_moduli.append(abs(delta))
            signed_imaginary.append(delta.imag)
            writer.writerow(
                [index + 1, row[2], ref.real, ref.imag, cand.real, cand.imag, delta.real, delta.imag, lower_bound]
            )
    reference_field = {(float(r[0]), float(r[1])): [float(v) for v in r[2:]] for r in raw_pattern}
    candidate_field = {(r["theta_deg"], r["phi_deg"]): [r[k] for k in CHANNELS] for r in field["samples"]}
    if (
        len(reference_field) != 2664
        or len(reference_field) != len(raw_pattern)
        or len(candidate_field) != len(field["samples"])
        or reference_field.keys() != candidate_field.keys()
    ):
        raise ValueError("Expected identical 37-by-72 angle grids without duplicates; no interpolation is applied")
    keys = sorted(reference_field)
    statistics = {
        channel: pattern_statistics(keys, [reference_field[k][i] for k in keys], [candidate_field[k][i] for k in keys])
        for i, channel in enumerate(CHANNELS)
    }
    with (output / "pattern-differences.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["theta_deg", "phi_deg", "channel", "ref_dbi", "candidate_dbi", "delta_db", "floor_status"])
        for key in keys:
            for index, channel in enumerate(CHANNELS):
                a, b = reference_field[key][index], candidate_field[key][index]
                state = "both_floor" if a == b == FLOOR else "one_floor" if FLOOR in (a, b) else "finite"
                writer.writerow([*key, channel, a, b, a - b if state == "finite" else "", state])
    peaks = (max(v[2] for v in reference_field.values()), max(v[2] for v in candidate_field.values()))
    azimuth = [(90, phi) for phi in range(0, 360, 5)]
    elevation = [(90 - a, 0) if a <= 90 else (a - 90, 180) if a <= 270 else (450 - a, 0) for a in range(0, 360, 5)]
    for plot_name, description, labels, cut in (
        ("azimuth", "XY cut: theta = 90 deg", ["+X", "+Y", "-X", "-Y"], azimuth),
        ("elevation", "XZ cut: phi = 0 and 180 deg", ["+X", "+Z", "-X", "-Z"], elevation),
    ):
        render_cut(
            output,
            plot_name,
            description,
            labels,
            [reference_field[k][2] for k in cut],
            [candidate_field[k][2] for k in cut],
            peaks,
            frequency_hz=field["frequency_hz"],
        )
    figure, axes = plt.subplots(1, 2, figsize=(11, 4))
    for axis, values, title in zip(
        axes,
        (signed_imaginary, residual_lower_bounds),
        ("Imaginary current: GAL minus candidate", "Residual lower bound beyond rounding"),
        strict=True,
    ):
        axis.plot([float(r[2]) for r in rows], [v * 1e6 for v in values], "o-", markersize=3)
        axis.set(title=title, xlabel="Pulse X (m)", ylabel="Current (microampere)")
        axis.grid(True)
    figure.suptitle(name)
    figure.tight_layout()
    for extension in ("png", "svg"):
        figure.savefig(output / f"current-residuals.{extension}", dpi=150)
    plt.close(figure)
    return {
        "case_id": name,
        "status": "DIFFERENCES_RECORDED",
        "compatibility_pass": None,
        "frequency_mhz": data["frequency_mhz"],
        "segments": data["segments"],
        "pulse_count": len(rows),
        "angle_samples": len(keys),
        "coordinate_max_abs_difference_m": max(coordinate_errors),
        "coordinates_within_export_rounding": True,
        "current_complex_error_mA": errors([v * 1000 for v in delta_moduli]),
        "current_outside_export_rounding": {"real": outside[0], "imaginary": outside[1]},
        "current_residual_lower_bound_uA": errors([v * 1e6 for v in residual_lower_bounds]),
        "mean_imaginary_delta_uA": math.fsum(signed_imaginary) / len(rows) * 1e6,
        "candidate_impedance_ohm": data["impedance_ohm"],
        "reference_impedance_ohm": None,
        "inferred_impedance": inferred_impedance(rows[data["source_index"]]),
        "pattern": statistics,
        "total_unique_direction_error_db": errors(
            [
                reference_field[k][2] - candidate_field[k][2]
                for k in keys
                if (k[0] not in (0, 180) or k[1] == 0)
                and reference_field[k][2] != FLOOR
                and candidate_field[k][2] != FLOOR
            ]
        ),
    }


def run(candidate: Path, reference: Path, output: Path, reference_version: str) -> dict[str, object]:
    manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
    for name in CASES:
        for relative in (f"{name}.json", f"capture-inputs/{name}-far-field.json", f"capture-inputs/{name}.maa"):
            if digest(candidate / relative) != manifest[relative]:
                raise ValueError(f"Candidate evidence changed: {relative}")
    output.mkdir(parents=True, exist_ok=False)
    snapshots = output / "reference"
    snapshots.mkdir()
    reports = []
    for name in CASES:
        expected = [name + suffix for suffix in (".maa", "-current.csv", "-pattern.csv")]
        missing = [filename for filename in expected if not (reference / filename).is_file()]
        for filename in expected:
            if filename not in missing:
                shutil.copyfile(reference / filename, snapshots / filename)
        if missing:
            reports.append({"case_id": name, "status": "AWAITING_REFERENCE", "missing": missing})
            continue
        if digest(snapshots / f"{name}.maa") != manifest[f"capture-inputs/{name}.maa"]:
            reports.append({"case_id": name, "status": "REFERENCE_INPUT_MISMATCH"})
            continue
        case_output = output / name
        case_output.mkdir()
        try:
            if any(not (snapshots / filename).read_bytes().strip() for filename in expected):
                raise ValueError("Empty reference file")
            report = compare_case(candidate, snapshots, case_output, name)
        except ValueError as exc:
            report = {"case_id": name, "status": "DATA_MISMATCH", "reason": str(exc)}
        write_json(case_output / "comparison.json", report)
        reports.append(report)
    result = {
        "status": "DIFFERENCES_RECORDED"
        if all(r["status"] == "DIFFERENCES_RECORDED" for r in reports)
        else "INCOMPLETE_BATCH",
        "compatibility_pass": None,
        "cases": reports,
        "reference_version": reference_version,
        "reference_version_source": "operator_supplied_cli",
        "reference_conditions_source": "Associated unchanged input MAA, filenames and declared version",
        "policy": "Finite angular samples equally weighted; floors counted separately; no fitting or interpolation",
        "rounding_policy": "Nearest-rounding intervals are diagnostics, not acceptance thresholds or confidence limits",
        "remaining": [
            "Direct GAL R/X/SWR/Ga",
            "GAL-resaved settings and executable identity",
            "Predefined compatibility tolerances",
            "No ground/material/taper/load coverage",
        ],
        "solver_calls": 0,
        "llm_calls": 0,
        "environment": {"python": platform.python_version(), "matplotlib": matplotlib.__version__},
        "source_hashes": {
            "candidate_manifest": digest(candidate / "manifest.json"),
            "batch_script": digest(Path(__file__)),
            "comparison_helpers": digest(Path(__file__).with_name("compare.py")),
        },
    }
    write_json(output / "comparison.json", result)
    for source in (Path(__file__), Path(__file__).with_name("compare.py")):
        shutil.copyfile(source, output / source.name)
    write_json(
        output / "manifest.json",
        {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True, help="Preserved diagnosis result directory")
    parser.add_argument("--reference", type=Path, required=True, help="Four supplied MAA/current/pattern file sets")
    parser.add_argument(
        "--reference-version", required=True, help="User-reported GAL version, not inferred from filenames"
    )
    parser.add_argument("--output", type=Path, required=True, help="New output directory; existing paths are refused")
    args = parser.parse_args()
    result = run(args.candidate, args.reference, args.output, args.reference_version)
    print(
        json.dumps(
            {
                "status": result["status"],
                "cases": [{"case_id": row["case_id"], "status": row["status"]} for row in result["cases"]],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if result["status"] == "INCOMPLETE_BATCH":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
