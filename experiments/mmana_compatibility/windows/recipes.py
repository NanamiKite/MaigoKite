"""Fixed physical fixtures shared by candidate solves and Windows reference capture.

This is a bounded experiment recipe, not a general MAA importer. GAL retains its
own automatic segmentation; candidate meshes are chosen independently.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

Point = tuple[float, float, float]
Wire = tuple[float, float, float, float, float, float, float]


@dataclass(frozen=True)
class Case:
    name: str
    ground: int = 0
    copper: bool = False
    resistor: bool = False
    refined: bool = False
    frequency_mhz: float = 7.1
    wires: tuple[Wire, ...] = ((-10, 0, 5, 10, 0, 5, 0.0005),)
    source: str = "w1c"
    source_position_m: Point = (0, 0, 5)
    front_phi_deg: int | None = None

    def maa(self, frequency: float) -> str:
        # Material enums are not portable: capture selects copper by its GUI caption.
        load = f"1,1\n{self.source},1,10,0" if self.resistor else "0,1"
        segmentation = "1600,160,2,1" if self.refined else "800,80,2,1"
        wires = "\n".join(",".join(f"{value:g}" for value in wire) + ",-1" for wire in self.wires)
        return (
            f"{self.name}\n*\n{frequency:.2f}\n***Wires***\n{len(self.wires)}\n{wires}\n"
            f"***Source***\n1,1\n{self.source},0,1\n***Load***\n{load}\n"
            f"***Segmentation***\n{segmentation}\n"
            f"***G/H/M/R/AzEl/X***\n{self.ground},0,0,50,120,60,0\n"
        ).replace("\n", "\r\n")


CASES = (
    Case("E01-free"),
    Case("E02-material", copper=True),
    Case("E03-resistor", resistor=True),
    Case("E04-ideal", ground=1),
    Case("E01-free-refined", refined=True),
    Case("E05-soil", ground=2),
)
GEOMETRY_CASES = (
    Case(
        "G01-inverted-v",
        wires=((-8, 0, 4, 0, 0, 10, 0.0005), (0, 0, 10, 8, 0, 4, 0.0005)),
        source="w1e",
        source_position_m=(0, 0, 10),
    ),
    Case(
        "G02-square-loop",
        wires=(
            (-5, -5, 0, 5, -5, 0, 0.0005),
            (5, -5, 0, 5, 5, 0, 0.0005),
            (5, 5, 0, -5, 5, 0, 0.0005),
            (-5, 5, 0, -5, -5, 0, 0.0005),
        ),
        source_position_m=(0, -5, 0),
    ),
    Case(
        "G03-yagi",
        frequency_mhz=145,
        wires=(
            (-0.52, -0.4, 0, 0.52, -0.4, 0, 0.001),
            (-0.5, 0, 0, 0.5, 0, 0, 0.001),
            (-0.47, 0.3, 0, 0.47, 0.3, 0, 0.001),
        ),
        source="w2c",
        source_position_m=(0, 0, 0),
        front_phi_deg=90,
    ),
)
ALL_CASES = (*CASES, *GEOMETRY_CASES)
# Four new points plus the archived 7.1 MHz centre form a five-point trend check.
# This deliberately does not promise precise resonance or bandwidth boundaries.
NEXT_SWEEP_MHZ = (6.8, 6.95, 7.25, 7.4)


@dataclass(frozen=True)
class CaptureJob:
    case: Case
    frequency_mhz: float
    full_pattern: bool

    @property
    def name(self) -> str:
        if self.frequency_mhz == self.case.frequency_mhz:
            return self.case.name
        return f"{self.case.name}-{self.frequency_mhz:.2f}MHz"


def capture_plan(*, next_batch: bool, sweep: bool, case_name: str | None) -> list[CaptureJob]:
    if next_batch and sweep:
        raise ValueError("Choose --next-batch or --sweep, not both.")
    if next_batch:
        jobs = [CaptureJob(case, case.frequency_mhz, True) for case in GEOMETRY_CASES]
        jobs += [
            CaptureJob(case, frequency, False) for case in CASES if not case.refined for frequency in NEXT_SWEEP_MHZ
        ]
    else:
        selected = ALL_CASES if case_name else CASES
        jobs = []
        for case in selected:
            jobs.append(CaptureJob(case, case.frequency_mhz, True))
            if sweep and not case.refined and case in CASES:
                jobs.extend(CaptureJob(case, value / 100, False) for value in range(680, 741, 5) if value != 710)
    if case_name:
        jobs = [job for job in jobs if job.case.name == case_name]
    if not jobs:
        raise ValueError("No captures selected; this case may already be complete in the previous batch.")
    return jobs


def check_saved_model(path: Path, case: Case, frequency: float) -> None:
    """Check GAL's saved physical inputs, independently of captions or screenshots."""
    lines = [line.strip() for line in path.read_bytes().decode("ascii", errors="replace").splitlines()]

    def section(label: str) -> int:
        return lines.index(label) + 1

    def values(index: int) -> list[float]:
        return [float(value.strip()) for value in lines[index].split(",")]

    saved_frequency = float(lines[2])
    if not math.isclose(saved_frequency, frequency, rel_tol=0, abs_tol=1e-6):
        raise ValueError(f"Saved frequency: expected {frequency:g}, saved {saved_frequency:g} MHz.")
    wire = section("***Wires***")
    if values(wire) != [len(case.wires)]:
        raise ValueError("Saved wire count differs from the recipe.")
    for index, expected in enumerate(case.wires, start=1):
        if values(wire + index) != [*expected, -1]:
            raise ValueError(f"Saved wire {index} geometry differs from the recipe.")
    source = section("***Source***")
    if values(source) != [1, 1] or re.sub(r"\s", "", lines[source + 1]).split(",")[0] != case.source:
        raise ValueError("Saved excitation differs from the recipe.")
    if [float(item) for item in lines[source + 1].split(",")[1:]] != [0, 1]:
        raise ValueError("Saved excitation phase/voltage differs from the recipe.")
    load = section("***Load***")
    if values(load) != [int(case.resistor), 1]:
        raise ValueError("Saved load count/enabled flag differs from the recipe.")
    if case.resistor:
        parts = [part.strip() for part in lines[load + 1].split(",")]
        if parts[0] != case.source or [float(item) for item in parts[1:]] != [1, 10, 0]:
            raise ValueError("Saved load is not feed-point R+jX = 10+j0 ohm.")
    expected_mesh = [1600, 160, 2, 1] if case.refined else [800, 80, 2, 1]
    if values(section("***Segmentation***")) != expected_mesh:
        raise ValueError("Saved segmentation differs from the recipe.")
    env = values(section("***G/H/M/R/AzEl/X***"))
    if len(env) != 7 or env[0] != case.ground or env[1] != 0 or env[3:] != [50, 120, 60, 0]:
        raise ValueError("Saved ground/height/reference settings differ from the recipe.")
    if (not case.copper and env[2] != 0) or (case.copper and env[2] == 0):
        raise ValueError("Saved conductor loss flag does not match GUI selection.")
