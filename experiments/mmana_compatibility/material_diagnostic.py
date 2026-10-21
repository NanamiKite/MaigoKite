"""Isolate copper constant and lumped/distributed loss semantics in two solves.

The legacy MMANA source places real wire resistance at its centre. Its copper
resistivity is a hypothesis for GAL, not an identified GAL implementation. All
impedance and field calculations here use public, unchanged pymininec APIs.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from compare import CHANNELS, digest, pattern_statistics, write_json
from engineering_probe import SOLVER_SHA256, Condition, solve
from mininec import mininec
from review_engineering import reference_field, reference_settings, verify_manifest

RESISTIVITY = 1.8e-8
SOURCE = "https://github.com/ja7ude/MMANA/blob/153ad346ee8748ca778d7d8926339a540ef40238/"


def whole_wire_skin_impedance(frequency_mhz: float) -> complex:
    """Evaluate the fixed 20 m wire's public skin-load formula, without a solve."""
    # A pulse covers a known physical length. Scale its public skin impedance
    # to the whole uniform wire, without fitting any observed GAL result.
    wire = mininec.Wire(160, -10, 0, 5, 10, 0, 5, 0.0005)
    model = mininec.Mininec(frequency_mhz, [wire])
    pulse = next(p for p in model.pulses if np.array_equal(p.point, (0, 0, 5)))
    skin = mininec.Skin_Effect_Load(wire, resistivity=RESISTIVITY)
    support_length = sum(float(np.linalg.norm(b - a)) for a, b in (pulse.dvecs(-0.5), pulse.dvecs(0.5)))
    return complex(skin.impedance(frequency_mhz, pulse) * 20 / support_length)


def run(reference: Path, output: Path) -> dict:
    verify_manifest(reference)
    reference_settings(reference / "cases" / "E02-material", "E02-material")
    if digest(Path(mininec.__file__)) != SOLVER_SHA256:
        raise ValueError("Use the unchanged solver version from the engineering capture")
    output.mkdir(parents=True, exist_ok=False)
    full_wire_impedance = whole_wire_skin_impedance(7.1)
    conditions = (
        Condition("distributed-legacy-resistivity", conductivity_s_per_m=1 / RESISTIVITY),
        Condition("centre-real-wire-resistance", feed_resistance_ohm=float(full_wire_impedance.real)),
    )
    field = reference_field(reference / "cases" / "E02-material", False)
    keys = sorted(field)
    cases = []
    for condition in conditions:
        directory = output / condition.name
        result = solve(condition, 7.1, 160, directory, pattern=True)
        raw = json.loads((directory / "far_field.json").read_text())
        candidate = {(r["theta_deg"], r["phi_deg"]): [r[c] for c in CHANNELS] for r in raw["samples"]}
        if candidate.keys() != field.keys():
            raise ValueError("Angular domains differ")
        comparison = pattern_statistics(keys, [field[k][2] for k in keys], [candidate[k][2] for k in keys])
        cases.append({"condition": condition.name, "result": result, "total_pattern": comparison})
    summary = {
        "status": "MATERIAL_SEMANTICS_DIAGNOSTIC",
        "compatibility_pass": None,
        "solver_runs": 2,
        "llm_calls_inside_program": 0,
        "reference_manifest_sha256": digest(reference / "manifest.json"),
        "solver_source_sha256": SOLVER_SHA256,
        "legacy_copper_resistivity_ohm_m": RESISTIVITY,
        "resistivity_basis": SOURCE + "ComLib.cpp#L1944-L1959",
        "legacy_lumped_loss_basis": SOURCE + "mininec3.cpp#L2294-L2304",
        "whole_wire_skin_impedance_ohm": {"r": float(full_wire_impedance.real), "x": float(full_wire_impedance.imag)},
        "cases": cases,
        "limits": [
            "Legacy MMANA source is not GAL source; GAL preset constants remain unconfirmed",
            "Public pymininec skin impedance replaces no solver algorithm and is not the legacy polynomial",
            "Lumped case intentionally drops internal reactance; it diagnoses legacy semantics, not physical accuracy",
            "Single straight wire at one frequency, not a production material compatibility mode",
        ],
    }
    write_json(output / "summary.json", summary)
    for name in (
        "material_diagnostic.py",
        "engineering_probe.py",
        "review_engineering.py",
        "review_semantics.py",
        "compare.py",
        "batch_compare.py",
        "probe.py",
    ):
        shutil.copy2(Path(__file__).with_name(name), output / name)
    write_json(
        output / "manifest.json",
        {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument(
        "--output", required=True, type=Path, help="New directory; existing evidence is not overwritten"
    )
    args = parser.parse_args()
    report = run(args.reference, args.output)
    print(
        json.dumps(
            {
                "status": report["status"],
                "solver_runs": report["solver_runs"],
                "cases": [
                    {
                        "condition": case["condition"],
                        "impedance_ohm": case["result"]["impedance_ohm"],
                        "peak_dbi": case["result"]["sampled_peak_gain_dbi"],
                        "total_pattern_rms_db": case["total_pattern"]["absolute_db"]["rms"],
                    }
                    for case in report["cases"]
                ],
                "artifacts": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
