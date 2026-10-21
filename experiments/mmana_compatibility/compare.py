"""Compare single-wire CSV captures without rerunning or adjusting a solver.

The parser is deliberately limited to these two observed MMANA-GAL formats.
Raw files remain authoritative; numerical floors are counted, not subtracted
as physical dB values. Plot clipping never changes comparison statistics.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import shutil
from decimal import Decimal
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

FLOOR = -999.0
CHANNELS = ("vertical_gain_dbi", "horizontal_gain_dbi", "total_gain_dbi")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def read_csv(path: Path, header: list[str]) -> list[list[str]]:
    rows = [row for row in csv.reader(path.read_text(encoding="gb18030").splitlines()) if row]
    if rows[0] != header:
        raise ValueError(f"Unexpected CSV header: {path.name}")
    # GAL far-field rows have a trailing comma, and the file has a blank final line.
    for row in rows[1:]:
        if len(row) not in (len(header), len(header) + 1) or any(row[len(header) :]):
            raise ValueError(f"Unexpected CSV row width: {path.name}")
        if not all(math.isfinite(float(value)) for value in row[: len(header)]):
            raise ValueError(f"Non-finite numerical input: {path.name}")
    return [row[: len(header)] for row in rows[1:]]


def errors(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "rms": math.sqrt(math.fsum(value * value for value in values) / len(values)),
        "max_abs": max(abs(value) for value in values),
    }


def pattern_statistics(
    keys: list[tuple[float, float]], reference: list[float], candidate: list[float]
) -> dict[str, object]:
    valid = [i for i, (a, b) in enumerate(zip(reference, candidate, strict=True)) if a != FLOOR and b != FLOOR]
    delta = [reference[i] - candidate[i] for i in valid]
    # The captured CSV prints two decimal places. Compare its quantization
    # floor with the measured difference before attributing that RMS to a
    # solver discrepancy. This diagnostic does not change acceptance limits.
    rounded = [round(candidate[i], 2) for i in valid]
    rounding_delta = [value - candidate[i] for i, value in zip(valid, rounded, strict=True)]
    largest = max(valid, key=lambda i: abs(reference[i] - candidate[i]))
    ref_peak, candidate_peak = max(reference), max(candidate)
    return {
        "absolute_db": errors(delta),
        "normalized_db": errors([value - ref_peak + candidate_peak for value in delta]),
        "largest_error_theta_phi_deg": keys[largest],
        "reference_peak_dbi": ref_peak,
        "candidate_peak_dbi": candidate_peak,
        "reference_floor_count": reference.count(FLOOR),
        "candidate_floor_count": candidate.count(FLOOR),
        "floor_mismatch_count": sum((a == FLOOR) != (b == FLOOR) for a, b in zip(reference, candidate, strict=True)),
        "outside_0_005_db_rounding_interval_count": sum(abs(value) > 0.005 + 1e-12 for value in delta),
        "candidate_rounding_only_db": errors(rounding_delta),
        "reference_matches_rounded_candidate_count": sum(
            abs(reference[i] - value) <= 1e-12 for i, value in zip(valid, rounded, strict=True)
        ),
    }


def render_cut(
    output: Path,
    name: str,
    description: str,
    labels: list[str],
    reference: list[float],
    candidate: list[float],
    peaks: tuple[float, float],
    *,
    frequency_hz: float = 145_000_000,
) -> None:
    figure, axes = plt.subplots(1, 2, subplot_kw={"projection": "polar"}, figsize=(12, 6))
    angles = [math.radians(value) for value in range(0, 361, 5)]
    for ax, normalized in zip(axes, (False, True), strict=True):
        for values, peak, label, style in zip(
            (reference, candidate), peaks, ("MMANA-GAL CSV", "pymininec 1.2.0"), ("-", "--"), strict=True
        ):
            plotted = [max(-40.0, value - (peak if normalized else 0.0)) for value in values]
            ax.plot(angles, plotted + plotted[:1], style, linewidth=1.8, label=f"{label}; peak {peak:.4f} dBi")
        ax.set_ylim(-40, 0 if normalized else 5)
        ax.set_yticks([-40, -30, -20, -10, 0])
        ax.set_xticks([0, math.pi / 2, math.pi, math.pi * 1.5])
        ax.set_xticklabels(labels)
        ax.set_title("Normalized dB (each peak = 0)" if normalized else "Absolute gain (dBi)", pad=20)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.32), fontsize=8)
    figure.suptitle(f"{frequency_hz / 1e6:g} MHz | Free space, lossless | {description}")
    figure.text(
        0.5,
        0.025,
        "Display floor: -40 dB only; 5-degree samples. No front/back reference axis is defined.",
        ha="center",
    )
    figure.tight_layout(rect=(0, 0.06, 1, 0.94))
    for extension in ("png", "svg"):
        figure.savefig(output / f"{name}.{extension}", dpi=160)
    plt.close(figure)


def run(
    candidate_dir: Path,
    current_csv: Path,
    pattern_csv: Path,
    output: Path,
    *,
    reference_version: str | None = None,
    reference_model: Path | None = None,
) -> dict[str, object]:
    manifest = json.loads((candidate_dir / "manifest.json").read_text(encoding="utf-8"))
    for name in ("input.json", "currents.json", "far_field.json"):
        if digest(candidate_dir / name) != manifest[name]:
            raise ValueError(f"Candidate evidence changed since capture: {name}")
    case = json.loads((candidate_dir / "input.json").read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(current_csv, output / "mmana-current.csv")
    shutil.copyfile(pattern_csv, output / "mmana-pattern.csv")
    if reference_model is not None:
        shutil.copyfile(reference_model, output / "reference.maa")
    current_rows = read_csv(
        output / "mmana-current.csv",
        ["导线", "脉冲", "X(m)", "Y(m)", "Z(m)", "实部(A)", "虚部(A)", "幅度(A)", "相位(度)"],
    )
    pattern_rows = read_csv(
        output / "mmana-pattern.csv", ["仰角(度)", "方位(度)", "垂直(dBi)", "水平(dBi)", "总共(dBi)"]
    )
    current_data = json.loads((candidate_dir / "currents.json").read_text(encoding="utf-8"))
    currents = current_data["samples"]
    field = json.loads((candidate_dir / "far_field.json").read_text(encoding="utf-8"))["samples"]
    candidate = {(row["theta_deg"], row["phi_deg"]): [row[channel] for channel in CHANNELS] for row in field}
    reference = {(float(row[0]), float(row[1])): [float(value) for value in row[2:]] for row in pattern_rows}
    if len(reference) != 2664 or len(pattern_rows) != len(reference) or reference.keys() != candidate.keys():
        raise ValueError("Expected identical 37-by-72 angle grids without duplicates; no interpolation is applied")
    expected_pulses = case["segments"] - 1
    if len(currents) != expected_pulses or len(current_rows) != expected_pulses:
        raise ValueError("Pulse count differs from the captured single-wire mesh")
    if case["segmentation"] not in {"uniform", "taper_both_ends"}:
        raise ValueError("Unsupported captured mesh kind")
    differences = []
    complex_errors = []
    outside_rounding = [0, 0]
    coordinate_errors = []
    for row, sample in zip(current_rows, currents, strict=True):
        if int(row[0]) != 1 or int(row[1]) != sample["pulse_index"] + 1:
            raise ValueError("Wire or pulse numbering differs")
        for printed, coordinate in zip(row[2:5], sample["position_m"], strict=True):
            distance = abs(float(printed) - coordinate)
            coordinate_errors.append(distance)
            # Fixed uniform fixtures have exactly printable coordinates.
            # Tapered coordinates are only known within each CSV rounding
            # cell; never replace the actual candidate mesh with those cells.
            half_step = float(Decimal(10) ** Decimal(printed).as_tuple().exponent) / 2
            limit = half_step if case["segmentation"] == "taper_both_ends" else 0.0
            if distance > limit + 1e-12:
                raise ValueError("Pulse coordinates differ beyond the declared coordinate policy")
        a, b = complex(float(row[5]), float(row[6])), complex(sample["real_a"], sample["imag_a"])
        delta = a - b
        complex_errors.append(abs(delta))
        for component, value in enumerate((delta.real, delta.imag)):
            half_step = float(Decimal(10) ** Decimal(row[5 + component]).as_tuple().exponent) / 2
            outside_rounding[component] += abs(value) > half_step + 1e-15
        differences.append([int(row[1]), float(row[2]), a.real, a.imag, b.real, b.imag, delta.real, delta.imag])
    with (output / "current-differences.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "pulse_1based",
                "x_m",
                "ref_real_a",
                "ref_imag_a",
                "candidate_real_a",
                "candidate_imag_a",
                "delta_real_a",
                "delta_imag_a",
            ]
        )
        writer.writerows(differences)
    keys = sorted(reference)
    statistics = {}
    for index, channel in enumerate(CHANNELS):
        statistics[channel] = pattern_statistics(
            keys, [reference[k][index] for k in keys], [candidate[k][index] for k in keys]
        )
    # Total power is independent of the polarization basis at the poles. Count
    # each physical pole once in this additional metric; retain every raw sample.
    unique = [k for k in keys if k[0] not in (0, 180) or k[1] == 0]
    unique_delta = [
        reference[k][2] - candidate[k][2] for k in unique if reference[k][2] != FLOOR and candidate[k][2] != FLOOR
    ]
    with (output / "pattern-differences.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "theta_deg",
                "phi_deg",
                "channel",
                "reference_dbi",
                "candidate_dbi",
                "delta_db",
                "floor_status",
            ]
        )
        for key in keys:
            for index, channel in enumerate(CHANNELS):
                a, b = reference[key][index], candidate[key][index]
                status = "both_floor" if a == b == FLOOR else "one_floor" if FLOOR in (a, b) else "finite"
                writer.writerow([*key, channel, a, b, a - b if status == "finite" else "", status])
    peaks = (max(row[2] for row in reference.values()), max(row[2] for row in candidate.values()))
    azimuth = [(90, phi) for phi in range(0, 360, 5)]
    # Angle here traverses the entire XZ plane from +X toward +Z. Elevation
    # alone cannot label both halves of a full vertical cut unambiguously.
    vertical = [(90 - a, 0) if a <= 90 else (a - 90, 180) if a <= 270 else (450 - a, 0) for a in range(0, 360, 5)]
    for name, description, labels, cut in (
        ("azimuth", "XY cut: theta = 90 deg", ["0 / +X", "90 / +Y", "180 / -X", "270 / -Y"], azimuth),
        ("elevation", "XZ cut: phi = 0 and 180 deg", ["+X", "+Z", "-X", "-Z"], vertical),
    ):
        render_cut(
            output,
            name,
            description,
            labels,
            [reference[k][2] for k in cut],
            [candidate[k][2] for k in cut],
            peaks,
            frequency_hz=case["frequency_hz"],
        )
    figure, axes = plt.subplots(1, 2, figsize=(10, 4))
    for axis, component, title in zip(axes, (2, 3), ("Real current", "Imaginary current"), strict=True):
        axis.plot(
            [row[1] for row in differences],
            [row[component] * 1000 for row in differences],
            "o-",
            label="MMANA-GAL CSV",
        )
        axis.plot(
            [row[1] for row in differences],
            [row[component + 2] * 1000 for row in differences],
            "--",
            label="pymininec 1.2.0",
        )
        axis.set(xlabel="Pulse X (m)", ylabel="Current (mA)", title=title)
        axis.grid(True)
        axis.legend()
    figure.suptitle(f"{case['case_id']} | 1 V, 0 deg source | CSV precision retained")
    figure.tight_layout()
    for extension in ("png", "svg"):
        figure.savefig(output / f"currents.{extension}", dpi=160)
    plt.close(figure)
    report = {
        "case_id": case["case_id"],
        "status": "DIFFERENCES_RECORDED",
        "compatibility_pass": None,
        "reference_identity": {"version": reference_version, "executable_sha256": None},
        "frequency_hz": case["frequency_hz"],
        "frequency_source": "Candidate input; reference CSV frequency/ground must be confirmed from associated MAA",
        "reference_model_semantics": "NOT_PARSED; supplied MAA is preserved for separate source/settings review",
        "angle_mapping": (
            "CSV first column labelled 仰角 is theta/zenith, per documented CSV order and this grid; "
            "phi from +X toward +Y"
        ),
        "mapping_limit": ("One straight wire cannot establish every coordinate symmetry or polarization handedness"),
        "error_sign": "MMANA CSV minus pymininec",
        "statistics_policy": (
            "Equal weight per finite grid sample, not solid-angle integral; -999 excluded and counted separately"
        ),
        "current": {
            "pulse_count": len(currents),
            "candidate_source_index_zero_based": current_data["source_index"],
            "candidate_source_position_m": current_data["source_position_m"],
            "reference_source_position_verified": False,
            "max_coordinate_difference_m": max(coordinate_errors),
            "coordinate_policy": "nearest-rounding cells for tapered mesh"
            if case["segmentation"] == "taper_both_ends"
            else "exact printable uniform-fixture coordinates (1e-12 m numerical tolerance)",
            "complex_absolute_error_a": errors(complex_errors),
            "outside_csv_half_last_digit": {"real": outside_rounding[0], "imaginary": outside_rounding[1]},
        },
        "pattern": statistics,
        "total_unique_physical_directions_db": errors(unique_delta),
        "rounding_note": (
            "Half-last-digit intervals describe export resolution, not a compatibility acceptance threshold"
        ),
        "front_to_back": None,
        "front_to_back_reason": (
            "No front/back reference axis defined for this capture; do not infer F/B from a single peak"
        ),
        "remaining": [
            "GAL executable/environment identity" if reference_version else "GAL version and executable identity",
            "Predefined acceptance tolerances",
            "Unresolved impedance/current differences",
            "No ground/material/load coverage or general automatic-mesh mapping",
        ],
        "environment": {"python": platform.python_version(), "matplotlib": matplotlib.__version__},
        "source_hashes": {
            "current_csv": digest(output / "mmana-current.csv"),
            "pattern_csv": digest(output / "mmana-pattern.csv"),
            "candidate_manifest": digest(candidate_dir / "manifest.json"),
            "candidate_current": digest(candidate_dir / "currents.json"),
            "candidate_pattern": digest(candidate_dir / "far_field.json"),
            "associated_mmana_model": digest(output / "reference.maa")
            if reference_model is not None
            else digest(candidate_dir / "mmana-test1.maa")
            if (candidate_dir / "mmana-test1.maa").is_file()
            else None,
            "associated_mmana_screenshot": digest(candidate_dir / "mmana-math.jpg")
            if (candidate_dir / "mmana-math.jpg").is_file()
            else None,
            "comparison_script": digest(Path(__file__)),
        },
    }
    write_json(output / "comparison.json", report)
    write_json(
        output / "manifest.json", {path.name: digest(path) for path in sorted(output.iterdir()) if path.is_file()}
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--current-csv", required=True, type=Path)
    parser.add_argument("--pattern-csv", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New artifact directory; existing paths are refused")
    parser.add_argument("--reference-version", help="Operator-reported GAL version")
    parser.add_argument(
        "--reference-maa", type=Path, help="Preserve the associated model; no semantic validation is implied"
    )
    args = parser.parse_args()
    report = run(
        args.candidate,
        args.current_csv,
        args.pattern_csv,
        args.output,
        reference_version=args.reference_version,
        reference_model=args.reference_maa,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
