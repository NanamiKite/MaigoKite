"""Prepare three fixed source/grid/coordinate captures, solving two candidates.

Uniform cases reuse the existing probe and the installed solver. Automatic GAL
segmentation is a reference-only capture until its actual mesh can be mapped;
no approximate taper or synthesized reference is substituted for that evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

from compare import CHANNELS, digest, plt, write_json
from mininec import mininec
from probe import run as probe

CASES = (
    ("A02-offset-uniform20", (-0.5, 0.0, 0.0), (0.5, 0.0, 0.0), (0.15, 0.0, 0.0), "w1c3"),
    ("A02-oblique-uniform20", (-0.18, -0.24, -0.4), (0.18, 0.24, 0.4), (0.0, 0.0, 0.0), "w1c"),
)


def write_maa(
    output: Path,
    name: str,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    source: str,
    segments: int,
) -> None:
    # Fixed capture fixtures, not a general MAA importer/exporter. Radius is
    # stored in metres; the GAL geometry UI displays it in millimetres.
    coordinates = ",".join(str(v) for v in (*start, *end))
    text = (
        f"{name}\n*\n145.0\n***Wires***\n1\n{coordinates},0.001,{segments}\n"
        f"***Source***\n1,1\n{source},0,1\n***Load***\n0,1\n"
        "***Segmentation***\n800,80,2.0,1\n***G/H/M/R/AzEl/X***\n0,0,0,50,120,60,0\n"
        "###Comment###\nFixed reference capture input; not a validated design.\n"
    )
    (output / f"{name}.maa").write_bytes(text.replace("\n", "\r\n").encode("ascii"))


def render_candidate(directory: Path, name: str) -> None:
    raw = json.loads((directory / "far_field.json").read_text(encoding="utf-8"))
    values = {(r["theta_deg"], r["phi_deg"]): r["total_gain_dbi"] for r in raw["samples"]}
    peak = max(values.values())
    azimuth = [(90, phi) for phi in range(0, 360, 5)]
    elevation = [(90 - a, 0) if a <= 90 else (a - 90, 180) if a <= 270 else (450 - a, 0) for a in range(0, 360, 5)]
    for title, cut, labels in (
        ("azimuth", azimuth, ["+X", "+Y", "-X", "-Y"]),
        ("elevation", elevation, ["+X", "+Z", "-X", "-Z"]),
    ):
        figure, axes = plt.subplots(1, 2, subplot_kw={"projection": "polar"}, figsize=(11, 5))
        for axis, normalized in zip(axes, (False, True), strict=True):
            gain = [max(-40, values[k] - (peak if normalized else 0)) for k in cut]
            axis.plot([math.radians(a) for a in range(0, 361, 5)], gain + gain[:1])
            axis.set_ylim(-40, 0 if normalized else 5)
            axis.set_xticks([0, math.pi / 2, math.pi, 1.5 * math.pi], labels)
            axis.set_title("Normalized dB; full sampled peak = 0" if normalized else "Absolute dBi")
        figure.suptitle(f"{name} | 145 MHz | pymininec candidate only\n{title}; GAL reference pending")
        figure.text(0.5, 0.015, "Display floor -40 dB; raw data unchanged; free space, lossless", ha="center")
        figure.tight_layout(rect=(0, 0.04, 1, 0.9))
        for extension in ("png", "svg"):
            figure.savefig(directory / f"{title}.{extension}", dpi=150)
        plt.close(figure)


def run(baseline: Path, output: Path) -> dict[str, object]:
    environment = json.loads((baseline / "environment.json").read_text(encoding="utf-8"))
    if digest(Path(mininec.__file__)) != environment["solver_source_sha256"]:
        raise ValueError("Solver source differs from the captured baseline")
    old_manifest = json.loads((baseline / "manifest.json").read_text(encoding="utf-8"))
    for filename in ("candidate.json", "environment.json", "input.json"):
        if digest(baseline / filename) != old_manifest[filename]:
            raise ValueError(f"Baseline evidence changed: {filename}")
    output.mkdir(parents=True, exist_ok=False)
    capture = output / "capture-inputs"
    capture.mkdir()
    reports = []
    old = json.loads((baseline / "candidate.json").read_text(encoding="utf-8"))
    for name, start, end, position, source in CASES:
        write_maa(capture, name, start, end, source, 20)
        result = probe(output / name, case_id=name, start=start, end=end, source_position=position)
        render_candidate(output / name, name)
        report = {
            **result,
            "maa_source_expression": source,
            "source_position_m": list(position),
            "source_expression_mapping_status": "AWAITING_GAL_REFERENCE",
            "input_maa_sha256": digest(capture / f"{name}.maa"),
        }
        if name == "A02-oblique-uniform20":
            z, reference_z = result["impedance_ohm"], old["impedance_ohm"]
            report["candidate_rotation_delta_ohm"] = {key: z[key] - reference_z[key] for key in ("r", "x")}
            report["wire_axis_unit_vector"] = [0.36, 0.48, 0.8]
            report["wire_axis_angles_deg"] = {
                "azimuth": math.degrees(math.atan2(0.48, 0.36)),
                "zenith": math.degrees(math.acos(0.8)),
            }
        write_json(output / name / "semantics.json", report)
        reports.append(report)
    auto = "A02-auto-segmentation"
    write_maa(capture, auto, (-0.5, 0.0, 0.0), (0.5, 0.0, 0.0), "w1c", -1)
    reports.append(
        {
            "case_id": auto,
            "candidate_status": "NOT_COMPUTED_UNMAPPED_SEGMENTATION",
            "mmana_comparison": "AWAITING_REFERENCE",
            "input_maa_sha256": digest(capture / f"{auto}.maa"),
            "required_evidence": "Actual GAL pulse coordinates/current CSV before choosing a mesh mapping",
        }
    )
    result = {
        "status": "AWAITING_REFERENCE",
        "compatibility_pass": None,
        "reference_version_requested": "1.2.0.20",
        "cases": reports,
        "solver_runs": 2,
        "llm_calls_inside_program": 0,
        "far_field_channels": CHANNELS,
        "source_mapping_basis": "https://hamsoft.ca/pages/mmana-gal/mmana-gal_basic_help/MMANA-GAL%20basic%20help.htm",
        "limits": [
            "Single lossless free-space wire only",
            "No material, ground, load or connector validation",
            "No general MAA or taper adapter",
        ],
    }
    write_json(output / "summary.json", result)
    shutil.copyfile(Path(__file__).with_name("SEMANTICS.md"), capture / "README.md")
    for name in ("semantics.py", "probe.py", "compare.py"):
        shutil.copyfile(Path(__file__).with_name(name), output / name)
    write_json(
        output / "manifest.json",
        {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory; existing paths are refused")
    args = parser.parse_args()
    result = run(args.baseline, args.output)
    print(json.dumps({"status": result["status"], "solver_runs": result["solver_runs"]}, indent=2))


if __name__ == "__main__":
    main()
