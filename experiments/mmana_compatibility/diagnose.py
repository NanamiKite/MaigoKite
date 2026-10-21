"""Bounded A01 sensitivity study using pymininec, NumPy and existing captures.

Historical coefficient substitution is an in-process diagnostic, not a GAL
solver mode or a production patch. Every substitution is restored. Frequency,
mesh and wire-partition variants are distinct cases, never fitted corrections
to the original reference. No MaigoKite or workflow runtime is imported.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path

import numpy as np
from compare import digest, plt, write_json
from mininec import mininec
from numpy.typing import NDArray

HISTORICAL_COMMIT = "153ad346ee8748ca778d7d8926339a540ef40238"
HISTORICAL_SHA = "bfc9e119ff11f71e61481a9e8d52227debaa7d0ff2c53da81fab5a0ee791e86d"


@dataclass(frozen=True)
class Case:
    name: str
    frequency_mhz: float = 145.0
    segments: int = 20
    height_m: float = 0.0
    partition: str = "single_wire"


def historical_elliptic_parameter(parameter: NDArray[np.float64]) -> NDArray[np.float64]:
    """Evaluate the historical polynomial with NumPy, only for sensitivity.

    Coefficients: original MMANA mininec3.cpp S28 and initialization at the
    pinned commit. This is not evidence of the GAL 1.2.0.20 implementation.
    """
    complement = 1 - parameter
    polynomial = np.polynomial.polynomial.polyval(
        complement, [1.38629436112, 0.09666344259, 0.03590092383, 0.03742563713, 0.01451196212]
    )
    logarithmic = np.polynomial.polynomial.polyval(
        complement, [0.5, 0.12498593397, 0.06880248576, 0.0332835346, 0.00441787012]
    )
    return polynomial - logarithmic * np.log(complement)


@contextmanager
def historical_coefficients(quadrature: bool, elliptic: bool) -> Iterator[None]:
    """Temporarily substitute coefficients without editing installed sources.

    The version/hash guard in run() bounds this use of integration internals;
    this context manager must not be promoted to a runtime compatibility mode.
    """
    original_cache, original_elliptic = mininec.legendre_cache.copy(), mininec.ellipk
    try:
        if quadrature:
            for order, points, weights in (
                (2, [0.288675135], [0.5]),
                (4, [0.430568156, 0.169990522], [0.173927423, 0.326072577]),
                (
                    8,
                    [0.480144928, 0.398333239, 0.262766205, 0.091717321],
                    [0.050614268, 0.111190517, 0.156853323, 0.181341892],
                ),
            ):
                mininec.legendre_cache[order] = [
                    np.array([sign * point for point in points for sign in (-1, 1)]),
                    np.repeat(weights, 2),
                ]
        if elliptic:
            mininec.ellipk = historical_elliptic_parameter
        yield
    finally:
        mininec.legendre_cache.clear()
        mininec.legendre_cache.update(original_cache)
        mininec.ellipk = original_elliptic


def solve(case: Case) -> tuple[mininec.Mininec, mininec.Excitation]:
    if case.partition == "single_wire":
        wires = [mininec.Wire(case.segments, -0.5, 0, case.height_m, 0.5, 0, case.height_m, 0.001)]
    else:
        # Keep the same straight physical conductor and total segment count.
        # Partition changes are measured, not silently used by an MAA importer.
        points = np.linspace(-0.5, 0.5, case.segments + 1)
        if case.partition == "float32_endpoints":
            points = points.astype(np.float32).astype(np.float64)
        wires = [
            mininec.Wire(1, a, 0, case.height_m, b, 0, case.height_m, 0.001)
            for a, b in zip(points[:-1], points[1:], strict=True)
        ]
    model = mininec.Mininec(case.frequency_mhz, wires, media=None)
    coordinates = np.array([pulse.point for pulse in model.pulses])
    centers = np.flatnonzero(np.all(np.isclose(coordinates, [0, 0, case.height_m], rtol=0, atol=1e-12), axis=1))
    if len(centers) != 1 or len(model.pulses) != case.segments - 1:
        raise ValueError("Unexpected pulse topology; do not guess an excitation index")
    source = mininec.Excitation(1, 0)
    model.register_source(source, int(centers[0]))
    model.compute()
    return model, source


def record(case: Case, model: mininec.Mininec, source: mininec.Excitation, output: Path) -> dict[str, object]:
    z = complex(source.impedance)
    reflection = abs((z - 50) / (z + 50))
    summary = {
        **asdict(case),
        "impedance_ohm": {"r": z.real, "x": z.imag},
        "swr_50": (1 + reflection) / (1 - reflection),
        "pulse_count": len(model.pulses),
        "source_index": int(source.idx),
        "source_voltage_v": [1, 0],
        "ground": "free_space",
        "radius_m": 0.001,
        "conductor": "perfect",
    }
    write_json(
        output / f"{case.name}.json",
        {
            **summary,
            "currents": [
                {"position_m": pulse.point.tolist(), "real_a": float(value.real), "imag_a": float(value.imag)}
                for pulse, value in zip(model.pulses, model.current, strict=True)
            ],
        },
    )
    return summary


def prepare_capture(case: Case, model: mininec.Mininec, output: Path) -> None:
    """Produce a fixed-case handoff, not a general MAA exporter or reference."""
    maa = (
        f"{case.name}\n*\n{case.frequency_mhz}\n***Wires***\n1\n"
        f"-0.5,0,0,0.5,0,0,0.001,{case.segments}\n***Source***\n1,1\nw1c,0,1\n"
        "***Load***\n0,1\n***Segmentation***\n800,80,2.0,1\n"
        "***G/H/M/R/AzEl/X***\n0,0,0,50,120,60,0\n###Comment###\n"
        "Candidate input for manual MMANA-GAL capture; not a reference result.\n"
    )
    (output / f"{case.name}.maa").write_bytes(maa.replace("\n", "\r\n").encode("ascii"))
    model.compute_far_field(mininec.Angle(0, 5, 37), mininec.Angle(0, 5, 72))
    vertical, horizontal, total = model.far_field.gain.T
    write_json(
        output / f"{case.name}-far-field.json",
        {
            "frequency_hz": round(case.frequency_mhz * 1e6),
            "coordinate_system": "theta from +Z; phi from +X toward +Y; degrees",
            "gain_unit": "dBi",
            "solver_floor_marker": -999,
            "samples": [
                {
                    "theta_deg": float(theta),
                    "phi_deg": float(phi),
                    "vertical_gain_dbi": float(v),
                    "horizontal_gain_dbi": float(h),
                    "total_gain_dbi": float(g),
                }
                for theta, phi, v, h, g in zip(
                    model.far_field.zen.flat,
                    model.far_field.azi.flat,
                    vertical.flat,
                    horizontal.flat,
                    total.flat,
                    strict=True,
                )
            ],
        },
    )
    (output / f"{case.name}-candidate.out").write_text(model.as_mininec() + "\n", encoding="utf-8")


def run(candidate: Path, historical_source: Path, output: Path) -> dict[str, object]:
    environment = json.loads((candidate / "environment.json").read_text(encoding="utf-8"))
    if (
        metadata.version("pymininec") != "1.2.0"
        or digest(Path(mininec.__file__)) != environment["solver_source_sha256"]
    ):
        raise ValueError("Solver version/source differs from the original baseline")
    if digest(historical_source) != HISTORICAL_SHA:
        raise ValueError("Historical source is not the reviewed pinned revision")
    manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
    for name in ("candidate.json", "currents.json", "environment.json", "input.json"):
        if digest(candidate / name) != manifest[name]:
            raise ValueError(f"Baseline evidence changed: {name}")
    output.mkdir(parents=True, exist_ok=False)
    captures = output / "capture-inputs"
    captures.mkdir()
    baseline_case = Case("baseline-145MHz-N20")
    baseline, source = solve(baseline_case)
    z0 = complex(source.impedance)
    original = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))["impedance_ohm"]
    repeat_delta = abs(z0 - complex(original["r"], original["x"]))
    if repeat_delta > 1e-12:
        raise ValueError("Baseline did not reproduce; stop before sensitivity comparisons")
    cases = [record(baseline_case, baseline, source, output)]
    mesh_values = [(20, z0)]
    for case in (
        Case("mesh-145MHz-N10", segments=10),
        Case("mesh-145MHz-N40", segments=40),
        Case("mesh-145MHz-N80", segments=80),
        Case("mesh-145MHz-N160", segments=160),
        Case("height-5m-free-space", height_m=5),
        Case("partition-float64", partition="float64_endpoints"),
        Case("partition-float32", partition="float32_endpoints"),
        Case("wavelength-sensitivity-SI", frequency_mhz=145 * 299.8 / 299.792458),
        Case("wavelength-sensitivity-300", frequency_mhz=145 * 299.8 / 300),
        Case("frequency-140MHz-N20", frequency_mhz=140),
        Case("frequency-150MHz-N20", frequency_mhz=150),
    ):
        model, excitation = solve(case)
        cases.append(record(case, model, excitation, output))
        if case.name.startswith("mesh-"):
            mesh_values.append((case.segments, complex(excitation.impedance)))
        if case.name in {"mesh-145MHz-N40", "mesh-145MHz-N80", "frequency-140MHz-N20", "frequency-150MHz-N20"}:
            prepare_capture(case, model, captures)
    for quadrature, elliptic, name in (
        (True, False, "historical-quadrature"),
        (False, True, "historical-elliptic"),
        (True, True, "historical-both"),
    ):
        with historical_coefficients(quadrature, elliptic):
            case = Case(name)
            model, excitation = solve(case)
            cases.append(record(case, model, excitation, output))
    precision = []
    for dtype in (np.complex64, np.complex128):
        current = np.linalg.solve(baseline.Z.astype(np.complex64).astype(dtype), baseline.rhs.astype(dtype))
        impedance = complex(1 / current[source.idx])
        precision.append(
            {
                "matrix_storage": "complex64",
                "solve_dtype": np.dtype(dtype).name,
                "impedance_ohm": {"r": impedance.real, "x": impedance.imag},
                "delta_from_baseline_ohm": {"r": (impedance - z0).real, "x": (impedance - z0).imag},
                "scope": "NumPy solve of rounded candidate matrix, not a reproduction of historical MMANA LU",
            }
        )
    mesh_values.sort()
    figure, axes = plt.subplots(1, 2, figsize=(10, 4))
    for axis, component, label, reference in zip(
        axes, ("real", "imag"), ("R (ohm)", "X (ohm)"), (72.97, 5.823), strict=True
    ):
        axis.plot([n for n, _ in mesh_values], [getattr(z, component) for _, z in mesh_values], "o-", label="pymininec")
        axis.plot([20], [reference], "x", markersize=9, label="GAL screenshot (N=20 only)")
        axis.set(xlabel="Uniform segment count", ylabel=label, xscale="log")
        axis.grid(True)
        axis.legend()
    figure.suptitle("145 MHz | 1 m lossless dipole | Free space | Mesh sensitivity, not convergence certification")
    figure.tight_layout()
    for extension in ("png", "svg"):
        figure.savefig(output / f"mesh-sensitivity.{extension}", dpi=160)
    plt.close(figure)
    report = {
        "case_id": "A01-uniform20",
        "status": "BOUNDED_DIAGNOSIS_RECORDED",
        "root_cause": "UNRESOLVED",
        "compatibility_pass": None,
        "reference_version": "1.2.0.20",
        "reference_version_source": "user_report",
        "reference_impedance_from_screenshot_ohm": {"r": 72.97, "x": 5.823},
        "repeat_impedance_abs_delta_ohm": repeat_delta,
        "cases": cases,
        "matrix_precision_sensitivity": precision,
        "solver_compute_calls_this_run": len(cases),
        "extra_numpy_solve_calls": len(precision),
        "llm_calls_inside_program": 0,
        "historical_source": {
            "url": f"https://github.com/ja7ude/MMANA/blob/{HISTORICAL_COMMIT}/mininec3.cpp",
            "sha256": HISTORICAL_SHA,
            "is_gal_1_2_0_20_source": False,
        },
        "interpretation_limits": [
            "Frequency variants measure wavelength sensitivity, not evidence of GAL constants",
            "Historical coefficient substitutions are diagnostics, not a supported solver mode",
            "Equivalent wire partitions must not be normalized away by an importer",
            "No convergence or material validity claimed from one coarse reference",
            "Next capture inputs have candidate results only; no fabricated GAL results",
        ],
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "dependencies": {item.metadata["Name"]: item.version for item in metadata.distributions()},
        },
        "source_hashes": {
            "solver": digest(Path(mininec.__file__)),
            "script": digest(Path(__file__)),
            "comparison_helpers": digest(Path(__file__).with_name("compare.py")),
            "original_manifest": digest(candidate / "manifest.json"),
            "reference_identity": digest(Path(__file__).with_name("reference_identity.json")),
        },
    }
    write_json(output / "diagnosis.json", report)
    shutil.copyfile(Path(__file__), output / "diagnose-source.py")
    write_json(
        output / "manifest.json",
        {str(path.relative_to(output)): digest(path) for path in sorted(output.rglob("*")) if path.is_file()},
    )
    return {
        key: report[key]
        for key in ("status", "root_cause", "repeat_impedance_abs_delta_ohm", "solver_compute_calls_this_run")
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--historical-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory; existing paths are refused")
    args = parser.parse_args()
    print(json.dumps(run(args.candidate, args.historical_source, args.output), indent=2))


if __name__ == "__main__":
    main()
