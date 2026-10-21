"""Compare the six engineering captures using independently chosen native meshes.

Archived candidate solves are reused unchanged. Current curves are interpolated
only within their shared spatial domain; far fields use identical angle keys.
The report separates material-model uncertainty from numerical differences.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import re
import shutil
from itertools import pairwise
from pathlib import Path, PurePosixPath

import numpy as np
from batch_compare import CURRENT_HEADER, PATTERN_HEADER, half_step
from compare import CHANNELS, FLOOR, digest, errors, pattern_statistics, plt, read_csv, write_json
from review_semantics import model_parameters

CASES = {
    "E01-free": ("free-lossless", 0, 0),
    "E01-free-refined": ("free-lossless", 0, 0),
    "E02-material": ("free-finite-conductivity", 0, 1),
    "E03-resistor": ("free-feed-resistor", 0, 0),
    "E04-ideal": ("ideal-ground", 1, 0),
    "E05-soil": ("lossy-ground", 2, 0),
}
Field = dict[tuple[float, float], list[float]]


def verify_manifest(directory: Path) -> None:
    for raw, expected in json.loads((directory / "manifest.json").read_text()).items():
        name = PurePosixPath(raw.replace("\\", "/"))
        if name.is_absolute() or ".." in name.parts:
            raise ValueError(f"Unsafe manifest member: {raw}")
        if digest(directory / name) != expected:
            raise ValueError(f"Evidence changed: {directory / name}")


def reference_settings(directory: Path, name: str) -> dict:
    """Validate this fixed recipe, not accept arbitrary MAA models or enums."""
    rows = model_parameters(directory / "model.maa")

    def section(label: str) -> list[str]:
        return rows[rows.index([label.lower()]) + 1]

    _, ground, material = CASES[name]
    if float(rows[0][0]) != 7.1 or section("***Wires***") != ["1"]:
        raise ValueError(f"Unexpected frequency or wire count: {name}")
    wire = rows[rows.index(["***wires***"]) + 2]
    if [float(v) for v in wire] != [-10, 0, 5, 10, 0, 5, 0.0005, -1]:
        raise ValueError(f"Geometry changed: {name}")
    source = rows[rows.index(["***source***"]) + 2]
    if section("***Source***") != ["1", "1"] or source != ["w1c", "0", "1"]:
        raise ValueError(f"Source changed: {name}")
    expected_loads = 1 if name == "E03-resistor" else 0
    if section("***Load***") != [str(expected_loads), "1"]:
        raise ValueError(f"Load count changed: {name}")
    if expected_loads:
        load = rows[rows.index(["***load***"]) + 2]
        if load[0] != "w1c" or [float(v) for v in load[1:]] != [1, 10, 0]:
            raise ValueError("Expected the observed GAL R+jX load at the physical centre")
    dm1, dm2 = (1600, 160) if name.endswith("refined") else (800, 80)
    if [float(v) for v in section("***Segmentation***")] != [dm1, dm2, 2, 1]:
        raise ValueError(f"Segmentation settings changed: {name}")
    if [float(v) for v in section("***G/H/M/R/AzEl/X***")] != [ground, 0, material, 50, 120, 60, 0]:
        raise ValueError(f"Environment changed: {name}")
    capture = json.loads((directory / "capture.json").read_text())
    if capture["status"] != "CAPTURED_REVIEW_REQUIRED" or capture["frequency_mhz"] != 7.1:
        raise ValueError(f"Incomplete reference: {name}")
    if name == "E05-soil":
        soil = capture["settings"]["soil"]
        expected = {
            "epsilon_r": 13,
            "conductivity_ms_per_m": 5,
            "x_m": 0,
            "height_m": 0,
            "media_count": 1,
            "ground_screen_enabled": False,
        }
        if any(soil.get(key) != value for key, value in expected.items()):
            raise ValueError("Ground settings do not match the candidate physical inputs")
    return capture["settings"]


def log_result(directory: Path) -> dict:
    text = (directory / "calculation.txt").read_text(encoding="utf-8")
    if "无计算错误" not in text:
        raise ValueError("Reference calculation did not finish successfully")
    row = next(line.split() for line in text.splitlines() if line.startswith("w1c"))
    if len(row) != 5 or row[1] != "1.00+j0.00":
        raise ValueError("Unexpected excitation/result log")
    match = re.fullmatch(r"([0-9.]+)([+-])j([0-9.]+)", row[3])
    pulses = re.search(r"脉冲总计\s*=\s*(\d+)", text)
    if match is None or pulses is None:
        raise ValueError("Unrecognized impedance or pulse count")
    return {
        "impedance_ohm": {"r": float(match[1]), "x": float(match[3]) * (1 if match[2] == "+" else -1)},
        "swr_z0_50": float(row[4]),
        "pulse_count": int(pulses[1]),
        "basis": "GAL calculation log, rounded to 0.01 ohm and 0.01 SWR; not inferred from current",
    }


def reference_field(directory: Path, ground: bool) -> Field:
    rows = read_csv(directory / "pattern.csv", PATTERN_HEADER)
    field = {(float(row[0]), float(row[1])): [float(v) for v in row[2:]] for row in rows}
    expected = {(t, p) for t in range(0, 91 if ground else 181, 5) for p in range(0, 360, 5)}
    if len(field) != len(rows) or field.keys() != expected:
        raise ValueError("Unexpected reference angular domain or duplicate samples")
    return field


def peak_summary(field: Field) -> dict:
    peak = max(v[2] for v in field.values())
    points = [key for key, value in field.items() if value[2] == peak]
    return {
        "sampled_peak_dbi": peak,
        "peak_theta_phi_deg": points,
        "peak_elevation_range_deg": [min(90 - t for t, _ in points), max(90 - t for t, _ in points)],
        "front_to_back_db": None,
        "front_to_back_reason": "Symmetric straight dipole without a unique forward axis",
    }


def currents(rows: list[list[str]], candidate: Path, output: Path) -> dict:
    data = json.loads((candidate / "currents.json").read_text())
    samples = data["samples"]
    x = np.array([s["position_m"][0] for s in samples])
    if not np.all(np.diff(x) > 0):
        raise ValueError("Candidate spatial grid is not ordered")
    records, deltas, skipped = [], [], []
    for row in rows:
        point = float(row[2])
        if not x[0] <= point <= x[-1]:
            skipped.append(int(row[1]))
            continue
        real = float(np.interp(point, x, [s["real_a"] for s in samples]))
        imag = float(np.interp(point, x, [s["imag_a"] for s in samples]))
        delta = complex(float(row[5]) - real, float(row[6]) - imag)
        deltas.append(abs(delta))
        records.append([int(row[1]), point, float(row[5]), float(row[6]), real, imag, delta.real, delta.imag])
    with (output / f"current-interpolated-{candidate.name}.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "pulse",
                "x_m",
                "gal_real_a",
                "gal_imag_a",
                "interp_real_a",
                "interp_imag_a",
                "delta_real_a",
                "delta_imag_a",
            ]
        )
        writer.writerows(records)
    centre = [row for row in rows if [float(v) for v in row[2:5]] == [0, 0, 5]]
    if len(centre) != 1:
        raise ValueError("Physical centre feed is not uniquely identified")
    feed = samples[data["feed_index_zero_based"]]
    if feed["position_m"] != [0, 0, 5]:
        raise ValueError("Candidate feed is not the same physical point")
    feed_delta = [float(centre[0][5]) - feed["real_a"], float(centre[0][6]) - feed["imag_a"]]
    return {
        "reference_pulses": len(rows),
        "candidate_pulses": len(samples),
        "interpolated_curve_absolute_error_a": errors(deltas),
        "excluded_reference_pulses_outside_candidate_domain": skipped,
        "policy": "Linear real/imag interpolation at printed reference X; no extrapolation or amplitude scaling. "
        "Curve differences include interpolation and reference coordinate/current rounding.",
        "feed_delta_a": {"real": feed_delta[0], "imag": feed_delta[1]},
        "feed_reference_half_last_digit_a": [half_step(v) for v in centre[0][5:7]],
    }


def render(output: Path, name: str, reference: Field, candidate: Field, ground: bool) -> None:
    peaks = (max(v[2] for v in reference.values()), max(v[2] for v in candidate.values()))
    for cut in ("azimuth", "elevation"):
        angles = list(range(0, 181 if ground and cut == "elevation" else 360, 5))
        keys = (
            [(75, a) for a in angles]
            if cut == "azimuth"
            else [(90 - a, 90) if a <= 90 else (a - 90, 270) if a <= 270 else (450 - a, 90) for a in angles]
        )
        figure, axes = plt.subplots(1, 2, figsize=(11, 5), subplot_kw={"projection": "polar"})
        for ax, normalized in zip(axes, (False, True), strict=True):
            for field, peak, label, style in zip(
                (reference, candidate), peaks, ("GAL 1.2.0.20", "pymininec N160"), ("-", "--"), strict=True
            ):
                gains = [max(-40, field[key][2] - (peak if normalized else 0)) for key in keys]
                radians = [math.radians(a) for a in angles]
                if len(angles) == 72:
                    radians += [2 * math.pi]
                    gains += gains[:1]
                ax.plot(radians, gains, style, label=f"{label}: peak {peak:.3f} dBi")
            ax.set_ylim(-40, 0 if normalized else max(5, math.ceil(max(peaks) / 5) * 5))
            ax.set_xticks(
                [0, math.pi / 2, math.pi, 1.5 * math.pi],
                ["+X", "+Y", "-X", "-Y"] if cut == "azimuth" else ["+Y", "+Z", "-Y", "-Z"],
            )
            if ground and cut == "elevation":
                ax.set_thetamax(180)
            ax.set_title("Normalized dB, own full-domain peak = 0" if normalized else "Absolute dBi")
            ax.legend(fontsize=7, loc="lower center", bbox_to_anchor=(0.5, -0.25))
        plane = "elevation 15 degrees" if cut == "azimuth" else "YZ plane (phi 90/270)"
        figure.suptitle(f"{name} | 7.1 MHz | {plane}\n5-degree sampling; display floor -40 dB; no unique front/back")
        figure.tight_layout(rect=(0, 0.06, 1, 0.9))
        for extension in ("png", "svg"):
            figure.savefig(output / f"{cut}.{extension}", dpi=150)
        plt.close(figure)


def compare_case(candidate: Path, reference: Path, output: Path, name: str) -> dict:
    condition, ground, _ = CASES[name]
    settings = reference_settings(reference, name)
    result = log_result(reference)
    rows = read_csv(reference / "current.csv", CURRENT_HEADER)
    if len(rows) != result["pulse_count"]:
        raise ValueError("Log and current CSV pulse counts differ")
    for index, row in enumerate(rows, 1):
        if [int(v) for v in row[:2]] != [1, index] or [float(v) for v in row[3:5]] != [0, 5]:
            raise ValueError("Unexpected current coordinate or pulse identity")
    if any(float(b[2]) <= float(a[2]) for a, b in pairwise(rows)):
        raise ValueError("Reference current coordinates are not ordered")
    field = reference_field(reference, bool(ground))
    output.mkdir()
    peak = peak_summary(field)
    write_json(
        output / "reference-far-field.json",
        {
            "frequency_hz": 7_100_000,
            "coordinate_system": "theta from +Z; phi from +X toward +Y; degrees",
            "gain_unit": "dBi",
            "solver_floor_marker": FLOOR,
            "polarization": "Original GAL V/H columns; pole basis is coordinate-dependent",
            "normalization_peak_dbi": peak["sampled_peak_dbi"],
            "samples": [
                {
                    "theta_deg": t,
                    "phi_deg": p,
                    **dict(zip(CHANNELS, values, strict=True)),
                    "normalized_total_db": values[2] - peak["sampled_peak_dbi"] if values[2] != FLOOR else None,
                }
                for (t, p), values in sorted(field.items())
            ],
        },
    )
    reviews = []
    for mesh in (40, 80, 160):
        directory = candidate / condition / f"N{mesh}"
        model = json.loads((directory / "input.json").read_text())
        if model["frequency_mhz"] != 7.1 or model["segments"] != mesh:
            raise ValueError("Candidate input does not match the fixed comparison")
        candidate_result = json.loads((directory / "result.json").read_text())
        raw = json.loads((directory / "far_field.json").read_text())
        other = {(r["theta_deg"], r["phi_deg"]): [r[c] for c in CHANNELS] for r in raw["samples"]}
        if other.keys() != field.keys() or len(other) != len(raw["samples"]):
            raise ValueError("Angle grids differ; do not interpolate fields to claim compatibility")
        keys = sorted(field)
        statistics = {
            channel: pattern_statistics(keys, [field[k][i] for k in keys], [other[k][i] for k in keys])
            for i, channel in enumerate(CHANNELS)
        }
        unique = [k for k in keys if k[0] not in (0, 180) or k[1] == 0]
        delta = [field[k][2] - other[k][2] for k in unique if FLOOR not in (field[k][2], other[k][2])]
        reviews.append(
            {
                "mesh": mesh,
                "candidate": candidate_result,
                "gal_minus_candidate_impedance_ohm": {
                    k: result["impedance_ohm"][k] - candidate_result["impedance_ohm"][k] for k in ("r", "x")
                },
                "gal_minus_candidate_swr": result["swr_z0_50"] - candidate_result["swr_z0_50"],
                "pattern": statistics,
                "unique_direction_total_error_db": errors(delta),
                "current": currents(rows, directory, output),
            }
        )
        if mesh == 160:
            render(output, name, field, other, bool(ground))
            with (output / "pattern-differences-N160.csv").open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["theta", "phi", "channel", "gal_dbi", "candidate_dbi", "delta_db", "floor"])
                for key in keys:
                    for i, channel in enumerate(CHANNELS):
                        a, b = field[key][i], other[key][i]
                        flag = "both" if a == b == FLOOR else "one" if FLOOR in (a, b) else "neither"
                        writer.writerow([*key, channel, a, b, a - b if flag == "neither" else "", flag])
            figure, axes = plt.subplots(1, 2, figsize=(10, 4))
            samples = json.loads((directory / "currents.json").read_text())["samples"]
            for ax, column, component in zip(axes, (5, 6), ("real_a", "imag_a"), strict=True):
                ax.plot([float(r[2]) for r in rows], [float(r[column]) * 1000 for r in rows], "o-", label="GAL native")
                ax.plot(
                    [s["position_m"][0] for s in samples],
                    [s[component] * 1000 for s in samples],
                    "--",
                    label="pymininec native N160",
                )
                ax.set(xlabel="X (m)", ylabel=f"{component} (mA)")
                ax.grid(True)
                ax.legend()
            figure.suptitle(f"{name} | 7.1 MHz | 1 V / 0 deg | independently chosen grids")
            figure.tight_layout()
            for extension in ("png", "svg"):
                figure.savefig(output / f"currents.{extension}", dpi=150)
            plt.close(figure)
    report = {
        "case": name,
        "status": "DIFFERENCES_RECORDED",
        "compatibility_pass": None,
        "reference": {**result, **peak, "settings": settings},
        "candidate_condition": condition,
        "material_equivalence": "UNVERIFIED_GAL_COPPER_PRESET" if name == "E02-material" else "LOSSLESS",
        "meshes": reviews,
        "statistics_policy": "GAL minus candidate; finite angle samples equally weighted, not solid-angle integral. "
        "-999 is counted separately. Independent native spatial grids, no pulse-by-pulse equality.",
    }
    write_json(output / "comparison.json", report)
    return report


def run(candidate: Path, reference: Path, output: Path) -> dict:
    verify_manifest(candidate)
    verify_manifest(reference)
    provenance = json.loads((reference / "provenance.json").read_text())
    identities = []
    for run_id in provenance["sources"]:
        record = json.loads((reference / "provenance" / run_id / "run.json").read_text())
        identities.append({k: record[k] for k in ("actual_exe_version", "exe_sha256", "script_sha256")})
    if len({(r["actual_exe_version"], r["exe_sha256"]) for r in identities}) != 1:
        raise ValueError("Different GAL binaries cannot be merged into one reference identity")
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(reference, output / "reference")
    reports = [compare_case(candidate, output / "reference" / "cases" / name, output / name, name) for name in CASES]
    summary = {
        "status": "ENGINEERING_DIFFERENCES_RECORDED",
        "compatibility_pass": None,
        "solver_runs": 0,
        "reused_candidate_solutions": 15,
        "llm_calls_inside_program": 0,
        "reference_identities": identities,
        "cases": reports,
        "candidate_source": {"path": str(candidate.resolve()), "manifest_sha256": digest(candidate / "manifest.json")},
        "reference_manifest_sha256": digest(reference / "manifest.json"),
        "environment": {"python": platform.python_version(), "numpy": np.__version__},
        "remaining": [
            "GAL copper material model/constant equivalence",
            "GAL frequency sweep not captured",
            "No validation of connected wires or other antenna topologies",
            "Engineering acceptance profile not approved",
        ],
    }
    write_json(output / "summary.json", summary)
    compact = [
        {
            "case": r["case"],
            "gal": r["reference"]["impedance_ohm"],
            "candidate_N160": r["meshes"][-1]["candidate"]["impedance_ohm"],
            "total_pattern_rms_db": r["meshes"][-1]["pattern"]["total_gain_dbi"]["absolute_db"]["rms"],
            "gal_peak_dbi": r["reference"]["sampled_peak_dbi"],
            "candidate_peak_dbi": r["meshes"][-1]["candidate"]["sampled_peak_gain_dbi"],
        }
        for r in reports
    ]
    write_json(output / "compact.json", {"status": summary["status"], "cases": compact, "solver_runs": 0})
    for name in ("review_engineering.py", "review_semantics.py", "compare.py", "batch_compare.py", "probe.py"):
        shutil.copy2(Path(__file__).with_name(name), output / name)
    write_json(
        output / "manifest.json",
        {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="New directory; previous evidence is never overwritten"
    )
    args = parser.parse_args()
    run(args.candidate, args.reference, args.output)
    print((args.output / "compact.json").read_text())


if __name__ == "__main__":
    main()
