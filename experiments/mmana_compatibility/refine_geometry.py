"""Bounded mesh diagnosis for three captured geometries using public solver APIs.

Six solves compare an independent finer uniform mesh and an independent native
end taper. Neither dimensions nor mesh parameters are fitted to reference data.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import time
from importlib import metadata
from pathlib import Path

from engineering_probe import SOLVER_SHA256
from geometry_probe import render_fields, solve
from mininec import mininec
from probe import digest, write_json
from review_engineering import reference_field, verify_manifest
from review_next import candidate_field, compare_solution, index_references, load, reference_result
from windows.recipes import GEOMETRY_CASES


def run(reference: Path, geometry: Path, output: Path) -> dict:
    refs = index_references([reference])
    verify_manifest(geometry)
    if metadata.version("pymininec") != "1.2.0" or digest(Path(mininec.__file__)) != SOLVER_SHA256:
        raise ValueError("Use the reviewed, unchanged pymininec 1.2.0 source")
    output.mkdir(parents=True, exist_ok=False)
    protocol = {
        "solver_run_limit": 6,
        "cases": [case.name for case in GEOMETRY_CASES],
        "meshes": [
            {"name": "N160", "segments_per_wire": 160, "kind": "uniform"},
            {
                "name": "T32",
                "segments_per_wire": 32,
                "kind": "native both-end taper",
                "minimum_segment_m": "max(4 * wire radius, wire length / 512)",
                "maximum_segment_m": "wire length / 16",
            },
        ],
        "invariants": "Frozen recipe geometry, physical feed, frequency, material and angles; no fitting",
        "stop": "Exactly these six solves; no automatic refinement or parameter search",
        "compatibility_pass": None,
        "llm_calls_inside_program": 0,
    }
    write_json(output / "protocol.json", protocol)
    started = time.monotonic()
    cases, compact = [], []
    for case in GEOMETRY_CASES:
        reference_case = refs[(case.name, case.frequency_mhz)]
        directory = output / case.name
        directory.mkdir()
        meshes = []
        fields = [
            ("GAL 1.2.0.20", reference_field(reference_case, False)),
            ("pymininec N80", candidate_field(geometry / case.name / "N80")),
        ]
        for name, count, tapered in (("N160", 160, False), ("T32", 32, True)):
            destination = directory / name
            actual = solve(case, count, destination, end_taper=tapered)
            comparison = compare_solution(case, reference_case, destination, directory)
            comparison["mesh"] = name
            meshes.append(comparison)
            fields.append((f"pymininec {name}", candidate_field(destination)))
            compact.append(
                {
                    "case": case.name,
                    "mesh": name,
                    "impedance_ohm": actual["impedance_ohm"],
                    "swr_z0_50": actual["swr_z0_50"],
                    "peak_gain_dbi": actual["sampled_peak_gain_dbi"],
                    "front_to_back_db": actual["front_to_back_db"],
                    "impedance_delta_ohm": comparison["impedance_delta_ohm_candidate_minus_reference"],
                    "total_pattern_db": comparison["pattern"]["total_gain_dbi"]["absolute_db"],
                    "linear_power": comparison["pattern"]["total_power_diagnostic"][
                        "linear_power_error_relative_to_reference_peak"
                    ],
                    "artifacts": {"raw": f"{case.name}/{name}", "plots": case.name},
                }
            )
            print(f"{case.name} {name}: {actual['impedance_ohm']}", flush=True)
        render_fields(directory, case, fields)
        previous = load(geometry / case.name / "N80" / "result.json")
        uniform = meshes[0]["candidate"]
        cases.append(
            {
                "case": case.name,
                "reference": reference_result(reference_case, case, case.frequency_mhz),
                "meshes": meshes,
                "uniform_n160_minus_n80_ohm": {
                    key: uniform["impedance_ohm"][key] - previous["impedance_ohm"][key] for key in ("r", "x")
                },
            }
        )
    summary = {
        "status": "MESH_DIFFERENCES_RECORDED",
        "solver_runs": 6,
        "llm_calls_inside_program": 0,
        "compatibility_pass": None,
        "elapsed_seconds": time.monotonic() - started,
        "cases": cases,
        "provenance": [
            {"root": str(root.resolve()), "manifest_sha256": digest(root / "manifest.json")}
            for root in (reference, geometry)
        ],
    }
    write_json(output / "summary.json", summary)
    write_json(output / "compact.json", {**{k: v for k, v in summary.items() if k != "cases"}, "cases": compact})
    write_json(
        output / "environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "solver_source_sha256": SOLVER_SHA256,
            "dependencies": {item.metadata["Name"]: item.version for item in metadata.distributions()},
        },
    )
    for name in (
        "refine_geometry.py",
        "geometry_probe.py",
        "review_next.py",
        "review_engineering.py",
        "review_semantics.py",
        "engineering_probe.py",
        "batch_compare.py",
        "compare.py",
        "probe.py",
        "windows/recipes.py",
    ):
        target = output / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(__file__).parent / name, target)
    write_json(
        output / "manifest.json",
        {path.relative_to(output).as_posix(): digest(path) for path in sorted(output.rglob("*")) if path.is_file()},
    )
    return {key: value for key, value in summary.items() if key not in ("cases", "provenance")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.reference, args.geometry, args.output), indent=2))
