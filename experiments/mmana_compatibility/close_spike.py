"""Close bounded current/material evidence gaps; do not implement a domain runtime.

Reuses all captured phasors and evaluates exactly five fixed copper-loss cases
through the existing solver. Signed-current comparisons use frozen wire order;
no phase fit, missing-row synthesis or reference-dependent geometry is allowed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import shutil
import time
from importlib import metadata
from pathlib import Path

import numpy as np
from batch_compare import CURRENT_HEADER, half_step
from compare import digest, errors, plt, read_csv, write_json
from engineering_probe import SOLVER_SHA256, Condition, solve
from material_diagnostic import RESISTIVITY, SOURCE, whole_wire_skin_impedance
from mininec import mininec, pulse
from review_engineering import verify_manifest
from review_next import compare_currents, index_references, load, reference_result
from windows.recipes import CASES, GEOMETRY_CASES, NEXT_SWEEP_MHZ, Case


def verify_current_basis(case: Case, candidate: Path) -> dict:
    """Reconstruct only the mesh to check the archived scalar-current direction."""
    config = load(candidate / "input.json")
    if config["wires"] != [list(wire) for wire in case.wires]:
        raise ValueError("Frozen physical wire ordering changed")
    wires = [mininec.Wire(config["segments_per_wire"], *wire) for wire in case.wires]
    if config.get("candidate_segmentation") == "native both-end taper":
        for wire, limits in zip(wires, config["taper_limits_by_wire"], strict=True):
            wire.segtype = 3
            wire.taper_min, wire.taper_max = limits["minimum_m"], limits["maximum_m"]
    model = mininec.Mininec(case.frequency_mhz, wires)
    archived = load(candidate / "currents.json")["samples"]
    for item, saved in zip(model.pulses, archived, strict=True):
        if not np.allclose(item.point, saved["position_m"], rtol=0, atol=1e-12):
            raise ValueError("Reconstructed mesh differs from archived currents")
        if sorted({int(w.n) + 1 for w in item.geo}) != saved["adjacent_wires_one_based"]:
            raise ValueError("Archived current connectivity differs")
        if list(item.sign) != [1, 1] or any(item.ground):
            raise ValueError("This diagnostic does not map reversed or grounded pulse bases")
    return {
        "pulse_count": len(archived),
        "direction_signs": [1, 1],
        "mesh_positions_and_adjacency_match": True,
        "basis": "Fixed end-to-start or isolated wires; same ordered geometry, no scalar sign conversion",
        "solver_runs": 0,
    }


def feed_evidence(case: Case, reference: Path) -> dict:
    rows = read_csv(reference / "current.csv", CURRENT_HEADER)
    matches = [
        row
        for row in rows
        if all(
            abs(float(text) - value) <= half_step(text) + 1e-12
            for text, value in zip(row[2:5], case.source_position_m, strict=True)
        )
    ]
    if len(matches) != 1:
        raise ValueError("Named physical feed does not have a unique reference current row")
    row = matches[0]
    result = reference_result(reference, case, case.frequency_mhz)
    z = complex(result["impedance_ohm"]["r"], result["impedance_ohm"]["x"])
    predicted = 1 / z
    # R/X are printed to 0.01 ohm. Bound inversion error as well as CSV rounding;
    # this checks source identity, not high-precision electromagnetic agreement.
    radius = math.hypot(0.005, 0.005)
    if abs(z) <= radius:
        raise ValueError("Rounded source impedance cannot identify the feed current")
    error_bound = radius / (abs(z) * (abs(z) - radius))
    consistent = all(
        abs(value - float(text)) <= half_step(text) + error_bound
        for value, text in zip((predicted.real, predicted.imag), row[5:7], strict=True)
    )
    if not consistent:
        raise ValueError("Feed CSV current is inconsistent with the named 1 V source and printed impedance")
    return {
        "source": case.source,
        "position_m": case.source_position_m,
        "export_wire": int(row[0]),
        "export_pulse": int(row[1]),
        "current_a": {"real": float(row[5]), "imag": float(row[6])},
        "rounded_voltage_over_impedance_consistent": consistent,
        "impedance_rounding_current_bound_a": error_bound,
        "log_pulse_count": result["pulse_count"],
        "export_count": len(rows),
        "missing_pulse_ids": sorted(set(range(1, result["pulse_count"] + 1)) - {int(row[1]) for row in rows}),
        "missing_policy": "No imputed current or inferred solver failure from export coverage alone",
    }


def material_sweep(refs: dict, engineering: Path, output: Path) -> list[dict]:
    free_case, copper_case = CASES[:2]
    rows = []
    for frequency in sorted((7.1, *NEXT_SWEEP_MHZ)):
        loss = whole_wire_skin_impedance(frequency)
        condition = Condition("legacy-centre-loss", feed_resistance_ohm=loss.real)
        directory = output / f"{frequency:.2f}MHz"
        actual = solve(condition, frequency, 80, directory, pattern=False)
        reference = reference_result(refs[(copper_case.name, frequency)], copper_case, frequency)
        free = reference_result(refs[(free_case.name, frequency)], free_case, frequency)
        baseline_path = engineering / "free-lossless" / ("N80" if frequency == 7.1 else f"sweep-{frequency:.2f}")
        baseline = load(baseline_path / "result.json")
        gal_loss = reference["impedance_ohm"]["r"] - free["impedance_ohm"]["r"]
        rows.append(
            {
                "frequency_mhz": frequency,
                "public_skin_impedance_whole_wire_ohm": {"r": loss.real, "x": loss.imag},
                "gal_copper_minus_lossless_r_ohm": gal_loss,
                "predicted_load_minus_gal_increment_ohm": loss.real - gal_loss,
                "actual": actual,
                "gal": reference,
                "actual_minus_gal_ohm": {
                    k: actual["impedance_ohm"][k] - reference["impedance_ohm"][k] for k in ("r", "x")
                },
                "actual_minus_candidate_lossless_ohm": {
                    k: actual["impedance_ohm"][k] - baseline["impedance_ohm"][k] for k in ("r", "x")
                },
            }
        )
    figure, axes = plt.subplots(1, 2, figsize=(10, 4))
    frequencies = [row["frequency_mhz"] for row in rows]
    axes[0].plot(frequencies, [row["gal_copper_minus_lossless_r_ohm"] for row in rows], "o", label="GAL increment")
    axes[0].plot(
        frequencies, [row["public_skin_impedance_whole_wire_ohm"]["r"] for row in rows], "-", label="Public skin R"
    )
    for key in ("r", "x"):
        axes[1].plot(
            frequencies, [row["actual_minus_gal_ohm"][key] for row in rows], "o-", label=f"Delta {key.upper()}"
        )
    for axis, label in zip(axes, ("Copper loss increment (ohm)", "Candidate minus GAL (ohm)"), strict=True):
        axis.set(xlabel="MHz", ylabel=label)
        axis.grid(True)
        axis.legend()
    figure.suptitle("Fixed 20 m wire | historical resistivity hypothesis, no fitting")
    figure.tight_layout()
    for extension in ("png", "svg"):
        figure.savefig(output / f"material-sweep.{extension}", dpi=140)
    plt.close(figure)
    return rows


def shared_current_domain(output: Path) -> list[dict]:
    """Compare meshes on the same exported locations, not unequal support counts."""
    reports = []
    for case in GEOMETRY_CASES:
        records = {}
        for mesh in ("N80", "T32"):
            with (output / case.name / f"currents-{mesh}.csv").open(encoding="utf-8", newline="") as stream:
                records[mesh] = {int(row["pulse"]): row for row in csv.DictReader(stream)}
        common = sorted(records["N80"].keys() & records["T32"].keys())
        meshes = {}
        for name, data in records.items():
            values = [data[key] for key in common]
            meshes[name] = {
                "complex_error_a": errors(
                    [math.hypot(float(row["delta_real_a"]), float(row["delta_imag_a"])) for row in values]
                ),
                "phase_error_deg": errors([float(row["phase_delta_deg"]) for row in values if row["phase_delta_deg"]]),
            }
        reports.append({"case": case.name, "common_reference_pulses": common, "meshes": meshes})
    return reports


def run(references: list[Path], geometry: Path, refined: Path, engineering: Path, output: Path) -> dict:
    refs = index_references(references)
    for directory in (geometry, refined, engineering):
        verify_manifest(directory)
    if metadata.version("pymininec") != "1.2.0" or digest(Path(mininec.__file__)) != SOLVER_SHA256:
        raise ValueError("Use the unchanged reviewed solver")
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "max_solver_runs": 5,
            "material_frequencies_mhz": sorted((7.1, *NEXT_SWEEP_MHZ)),
            "material_resistivity_ohm_m": RESISTIVITY,
            "material_model": "Public whole-wire skin R at centre; X discarded only in this explicit diagnostic",
            "source": SOURCE + "ComLib.cpp#L1944-L1959",
            "current_policy": "Reuse N80/T32, frozen wire order; no sign/phase fit or synthesized missing row",
            "llm_calls_inside_program": 0,
        },
    )
    started = time.monotonic()
    currents = []
    for case in GEOMETRY_CASES:
        reference = refs[(case.name, case.frequency_mhz)]
        destination = output / case.name
        destination.mkdir()
        record = {"case": case.name, "feed": feed_evidence(case, reference), "meshes": []}
        for root, name in ((geometry, "N80"), (refined, "T32")):
            candidate = root / case.name / name
            basis = verify_current_basis(case, candidate)
            comparison = compare_currents(reference, candidate, case, destination)
            comparison.pop("reference_export_pulse_ids")
            record["meshes"].append({"mesh": name, "basis": basis, **comparison})
        currents.append(record)
    write_json(output / "common-domain.json", shared_current_domain(output))
    material_dir = output / "material"
    material_dir.mkdir()
    materials = material_sweep(refs, engineering, material_dir)
    summary = {
        "status": "BOUNDED_SEMANTICS_REVIEW_COMPLETE",
        "compatibility_pass": None,
        "solver_runs": 5,
        "llm_calls_inside_program": 0,
        "elapsed_seconds": time.monotonic() - started,
        "currents": currents,
        "material_sweep": materials,
        "limits": [
            "Reference CSV is rounded and loop export omits pulse 88; no complete-loop current certification",
            "No reverse-wire, ground-connection, branching or multiple-source phase evidence",
            "Historical material representation is not physical distributed loss or proof of GAL implementation",
            "Five-point single-wire sweep, not broadband, tubing, insulation or thermal validation",
        ],
        "provenance": [
            {"root": str(path.resolve()), "manifest_sha256": digest(path / "manifest.json")}
            for path in (*references, geometry, refined, engineering)
        ],
    }
    write_json(output / "summary.json", summary)
    write_json(
        output / "compact.json",
        {
            "status": summary["status"],
            "compatibility_pass": None,
            "solver_runs": 5,
            "llm_calls_inside_program": 0,
            "limits": summary["limits"],
            "currents": [
                {
                    "case": row["case"],
                    "missing_pulse_ids": row["feed"]["missing_pulse_ids"],
                    "meshes": [
                        {key: mesh[key] for key in ("mesh", "complex_error_a", "wrapped_phase_error_deg")}
                        for mesh in row["meshes"]
                    ],
                }
                for row in currents
            ],
            "material": [
                {
                    key: row[key]
                    for key in ("frequency_mhz", "predicted_load_minus_gal_increment_ohm", "actual_minus_gal_ohm")
                }
                for row in materials
            ],
            "artifacts": {"full_summary": "summary.json", "material_plot": "material/material-sweep.png"},
        },
    )
    write_json(
        output / "environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "solver_source_sha256": SOLVER_SHA256,
            "pulse_source_sha256": digest(Path(pulse.__file__)),
            "dependencies": {item.metadata["Name"]: item.version for item in metadata.distributions()},
        },
    )
    for name in (
        "close_spike.py",
        "review_next.py",
        "material_diagnostic.py",
        "geometry_probe.py",
        "engineering_probe.py",
        "review_engineering.py",
        "review_semantics.py",
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
    return {key: summary[key] for key in ("status", "solver_runs", "elapsed_seconds", "compatibility_pass")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, nargs="+", required=True)
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument("--refined", type=Path, required=True)
    parser.add_argument("--engineering", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.references, args.geometry, args.refined, args.engineering, args.output), indent=2))
