"""Review the three fixed A02 captures and one reference-guided taper candidate.

Uniform candidates are reused unchanged. The taper calculation borrows only
the observed pulse count, not currents or gain, and uses public solver options.
It diagnoses mesh representability, not a general MMANA meshing implementation.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from decimal import Decimal, InvalidOperation
from pathlib import Path

from batch_compare import CURRENT_HEADER, PATTERN_HEADER, half_step
from compare import CHANNELS, FLOOR, digest, errors, plt, read_csv, write_json
from compare import run as compare
from mininec import mininec, taper
from probe import run as probe

UNIFORM = ("A02-offset-uniform20", "A02-oblique-uniform20")
AUTO = "A02-auto-segmentation"


def model_parameters(path: Path) -> list[list[str]]:
    """Normalize numeric spelling in fixed fixtures, not import general MAA."""
    lines = path.read_text(encoding="gb18030").splitlines()
    if lines[1] != "*" or "###Comment###" not in lines:
        raise ValueError("Unexpected fixed-fixture MAA format")
    rows = []
    for row in csv.reader(lines[2 : lines.index("###Comment###")]):
        fields = []
        for field in row:
            field = field.strip()
            try:
                number = Decimal(field)
            except InvalidOperation:
                fields.append(field.lower())
            else:
                if not number.is_finite():
                    raise ValueError("Non-finite MAA parameter")
                fields.append(str(number.normalize()))
        rows.append(fields)
    return rows


def verify_manifest(directory: Path) -> None:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest.items():
        if digest(directory / name) != expected:
            raise ValueError(f"Evidence changed: {directory / name}")


def source_evidence(rows: list[list[str]], index: int, display: dict[str, str]) -> dict[str, object]:
    # UI R/X and source current are independently rounded. Check whether the
    # predicted 1/Z from displayed impedance lies in the CSV rounding cell;
    # this is corroboration of the named source, not a high-precision oracle.
    current = 1 / complex(float(display["r"]), float(display["x"]))
    row = rows[index]
    return {
        "pulse_1based": index + 1,
        "csv_position_m": [float(v) for v in row[2:5]],
        "csv_current_a": row[5:7],
        "displayed_impedance_predicts_csv_current": all(
            abs(value - float(text)) <= half_step(text) + 1e-15
            for value, text in zip((current.real, current.imag), row[5:7], strict=True)
        ),
        "basis": "Named source in saved MAA and screenshot, CSV pulse coordinates, rounded 1/Z cross-check",
    }


def residual_summary(candidate: Path, reference: Path) -> dict[str, object]:
    rows = read_csv(reference, CURRENT_HEADER)
    samples = json.loads((candidate / "currents.json").read_text(encoding="utf-8"))["samples"]
    residuals = []
    for row, sample in zip(rows, samples, strict=True):
        residuals.append(
            math.hypot(
                *(
                    max(0.0, abs(float(text) - value) - half_step(text))
                    for text, value in zip(row[5:7], (sample["real_a"], sample["imag_a"]), strict=True)
                )
            )
            * 1e6
        )
    return {"microampere": errors(residuals), "meaning": "Lower bound beyond nearest-rounding cells, not true error"}


def mesh_review(rows: list[list[str]], output: Path) -> dict[str, object]:
    count = len(rows) + 1
    minimum = mininec.Mininec.c / 145 / 800
    options = (
        ("solver_default", {}),
        ("documented_dm1_start", {"min_t": minimum}),
        ("direct_dm1_dm2_bounds", {"min_t": minimum, "max_t": mininec.Mininec.c / 145 / 80}),
    )
    positions = [float(row[2]) for row in rows]
    reports = []
    figure, axes = plt.subplots(1, 2, figsize=(11, 4))
    for label, kwargs in options:
        pieces = list(taper.taper2(-0.5, 0.5, count, 0.001, **kwargs))
        points = [float(end) for _, end in pieces[:-1]]
        deltas = [a - b for a, b in zip(points, positions, strict=True)]
        outside = sum(abs(d) > half_step(row[2]) + 1e-12 for d, row in zip(deltas, rows, strict=True))
        reports.append(
            {
                "option": label,
                "kwargs": kwargs,
                "pulse_positions_m": points,
                "max_distance_to_printed_position_m": max(abs(d) for d in deltas),
                "outside_coordinate_rounding_count": outside,
            }
        )
        axes[0].plot(range(1, len(rows) + 1), [d * 1000 for d in deltas], label=label)
    axes[0].axhspan(-0.5, 0.5, color="grey", alpha=0.2, label="CSV rounding +/-0.5 mm")
    axes[0].set(xlabel="Pulse number", ylabel="Candidate minus printed X (mm)", title="Public taper options")
    axes[0].legend(fontsize=7)
    axes[1].plot(positions, [float(row[5]) * 1000 for row in rows], label="Real")
    axes[1].plot(positions, [float(row[6]) * 1000 for row in rows], label="Imaginary")
    axes[1].set(xlabel="Printed pulse X (m)", ylabel="GAL current (mA)", title="Actual reference current")
    axes[1].legend()
    figure.suptitle(f"145 MHz | SEG=-1 | observed {len(rows)} pulses / {count} segments")
    figure.tight_layout()
    for extension in ("png", "svg"):
        figure.savefig(output / f"mesh-options.{extension}", dpi=150)
    plt.close(figure)
    return {
        "segment_count": count,
        "segment_count_basis": "Observed GAL CSV pulse count + 1 for this isolated straight wire",
        "taper_min_m": minimum,
        "options": reports,
        "status": "REFERENCE_GUIDED_MESH_DIAGNOSTIC",
        "limits": ["No automatic DM2-to-segment-count rule", "SC=2 and EC=1 only", "CSV coordinates rounded to 1 mm"],
    }


def export_reference_field(path: Path, output: Path) -> None:
    rows = read_csv(path, PATTERN_HEADER)
    field = {(float(r[0]), float(r[1])): [float(v) for v in r[2:]] for r in rows}
    if len(rows) != 2664 or len(field) != len(rows):
        raise ValueError("Unexpected reference angular grid")
    peak = max(v[2] for v in field.values())
    # This normalized numeric export preserves the reference floor marker and
    # the original CSV. It is not inferred from the screenshot or a plot.
    write_json(
        output / "reference-far-field.json",
        {
            "frequency_hz": 145_000_000,
            "coordinate_system": "theta from +Z; phi from +X toward +Y; degrees",
            "gain_unit": "dBi",
            "normalization": "full sampled total-gain peak",
            "peak_dbi": peak,
            "solver_floor_marker": FLOOR,
            "samples": [
                {
                    "theta_deg": key[0],
                    "phi_deg": key[1],
                    **dict(zip(CHANNELS, values, strict=True)),
                    "normalized_total_db": values[2] - peak if values[2] != FLOOR else None,
                }
                for key, values in sorted(field.items())
            ],
        },
    )


def run(candidate: Path, reference: Path, displays: Path, output: Path) -> dict[str, object]:
    verify_manifest(candidate)
    verify_manifest(reference)
    environment = json.loads((candidate / UNIFORM[0] / "environment.json").read_text(encoding="utf-8"))
    if digest(Path(mininec.__file__)) != environment["solver_source_sha256"]:
        raise ValueError("Solver source differs from the original uniform candidates")
    output.mkdir(parents=True, exist_ok=False)
    snapshots = output / "reference"
    snapshots.mkdir()
    cases = (*UNIFORM, AUTO)
    for name in cases:
        for suffix in (".maa", "-gal.maa", "-current.csv", "-pattern.csv", "-result.png"):
            shutil.copyfile(reference / f"{name}{suffix}", snapshots / f"{name}{suffix}")
        if digest(snapshots / f"{name}.maa") != digest(candidate / "capture-inputs" / f"{name}.maa"):
            raise ValueError(f"Supplied input changed: {name}")
        if model_parameters(snapshots / f"{name}.maa") != model_parameters(snapshots / f"{name}-gal.maa"):
            raise ValueError(f"GAL-resaved settings changed: {name}")
    shutil.copyfile(displays, snapshots / "screenshot-values.json")
    displayed = json.loads((snapshots / "screenshot-values.json").read_text(encoding="utf-8"))
    for name in cases:
        if digest(snapshots / f"{name}-result.png") != displayed["cases"][name]["screenshot_sha256"]:
            raise ValueError(f"Screenshot transcription provenance mismatch: {name}")
    reports = []
    for name in cases:
        case_candidate = candidate / name
        mesh = None
        if name == AUTO:
            auto_dir = output / "mesh-diagnostic"
            auto_dir.mkdir()
            rows = read_csv(snapshots / f"{name}-current.csv", CURRENT_HEADER)
            mesh = mesh_review(rows, auto_dir)
            write_json(auto_dir / "mesh.json", mesh)
            if mesh["options"][1]["outside_coordinate_rounding_count"]:
                raise ValueError("Documented-start taper does not represent the observed mesh; no solve attempted")
            case_candidate = output / "reference-guided-candidate"
            probe(case_candidate, case_id=AUTO, segments=mesh["segment_count"], taper_min_m=mesh["taper_min_m"])
        comparison = compare(
            case_candidate,
            snapshots / f"{name}-current.csv",
            snapshots / f"{name}-pattern.csv",
            output / name,
            reference_version="1.2.0.20",
            reference_model=snapshots / f"{name}-gal.maa",
        )
        export_reference_field(snapshots / f"{name}-pattern.csv", output / name)
        result = json.loads((case_candidate / "candidate.json").read_text(encoding="utf-8"))
        rows = read_csv(snapshots / f"{name}-current.csv", CURRENT_HEADER)
        display = displayed["cases"][name]
        review = {
            "case_id": name,
            "status": "REFERENCE_GUIDED_DIFFERENCES_RECORDED" if mesh else "DIFFERENCES_RECORDED",
            "saved_model_parameters_match_input": True,
            "source": source_evidence(rows, result["source_index_zero_based"], display),
            "screenshot_result": display,
            "candidate_impedance_ohm": result["impedance_ohm"],
            "display_minus_candidate_ohm": {k: float(display[k]) - result["impedance_ohm"][k] for k in ("r", "x")},
            "current_residual_lower_bound": residual_summary(case_candidate, snapshots / f"{name}-current.csv"),
            "pattern": comparison["pattern"],
            "mesh_diagnostic": mesh,
            "compatibility_pass": None,
        }
        write_json(output / name / "review.json", review)
        reports.append(review)
    summary = {
        "status": "DIFFERENCES_RECORDED_WITH_REFERENCE_GUIDED_TAPER_DIAGNOSTIC",
        "compatibility_pass": None,
        "reference_version": "1.2.0.20",
        "reference_version_source": "User report; executable hash not supplied",
        "cases": reports,
        "solver_runs": 1,
        "reused_uniform_candidates": 2,
        "llm_calls_inside_program": 0,
        "solver_source_sha256": digest(Path(mininec.__file__)),
        "taper_source_sha256": digest(Path(taper.__file__)),
        "candidate_manifest_sha256": digest(candidate / "manifest.json"),
        "scope": "Single lossless free-space wire; no materials, ground, connections or loads",
    }
    write_json(output / "summary.json", summary)
    for name in ("review_semantics.py", "probe.py", "compare.py", "batch_compare.py"):
        shutil.copyfile(Path(__file__).with_name(name), output / name)
    write_json(
        output / "manifest.json",
        {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--screenshot-values", required=True, type=Path, help="Manual transcription with image hashes")
    parser.add_argument(
        "--output", required=True, type=Path, help="New directory; existing evidence is never overwritten"
    )
    args = parser.parse_args()
    result = run(args.candidate, args.reference, args.screenshot_values, args.output)
    print(json.dumps({"status": result["status"], "solver_runs": result["solver_runs"]}, indent=2))


if __name__ == "__main__":
    main()
