"""Read/write the reviewed MAA subset without translating GAL's automatic mesh.

YAAS's importer targets NEC geometry and uniform meshing, not these MININEC
source/junction semantics. This small mapping rejects everything it cannot
preserve. The caller archives the original bytes, including comments/settings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import EngineeringRequest, Feed, Ground, Material, Mesh, Wire


@dataclass(frozen=True)
class ImportedMaa:
    request: EngineeringRequest
    original_segmentation: tuple[float, ...]
    original_wire_segments: tuple[int, ...]


def read_maa(text: str, mesh: Mesh) -> ImportedMaa:
    """Require an explicit candidate mesh; MMANA pulse counts are not portable."""
    lines = [line.strip() for line in text.splitlines()]
    cursor = 0

    def take() -> str:
        nonlocal cursor
        if cursor >= len(lines):
            raise ValueError("Truncated MAA input")
        result = lines[cursor]
        cursor += 1
        return result

    def expect(label: str) -> None:
        if take() != label:
            raise ValueError(f"Expected MAA section {label}; unknown sections are not discarded")

    def fields(count: int) -> list[str]:
        values = [part.strip() for part in take().split(",")]
        if len(values) != count:
            raise ValueError(f"Expected {count} MAA values at line {cursor}")
        return values

    name = take()
    expect("*")
    frequency = float(take()) * 1e6
    expect("***Wires***")
    count = int(take())
    if not 1 <= count <= 16:
        raise ValueError("MAA wire count must be between 1 and 16")
    wires, segments = [], []
    for _ in range(count):
        values = fields(8)
        numbers = [float(value) for value in values[:7]]
        segment_count = int(values[7])
        if segment_count != -1 and (segment_count < 4 or segment_count % 2):
            raise ValueError("Only automatic -1 or even positive MAA wire SEG values are supported")
        segments.append(segment_count)
        wires.append(
            Wire(
                start_m=(numbers[0], numbers[1], numbers[2]),
                end_m=(numbers[3], numbers[4], numbers[5]),
                radius_m=numbers[6],
            )
        )
    expect("***Source***")
    if fields(2) != ["1", "1"]:
        raise ValueError("Only one enabled voltage source is supported")
    source, phase, voltage = fields(3)
    match = re.fullmatch(r"w([1-9][0-9]*)(c|e)", source)
    if match is None:
        raise ValueError("Only w<number>c and connected w<number>e source positions are supported")
    expect("***Load***")
    load_count, load_mode = fields(2)
    resistance = 0.0
    if load_mode != "1" or load_count not in {"0", "1"}:
        raise ValueError("Unsupported MAA load configuration")
    if load_count == "1":
        location, mode, r, x = fields(4)
        if location != source or mode != "1" or float(x) != 0:
            raise ValueError("Only a pure series resistance at the feed is supported; RLC/Q/traps are not")
        resistance = float(r)
    expect("***Segmentation***")
    segmentation = tuple(float(value) for value in fields(4))
    if segmentation not in {(800, 80, 2, 1), (1600, 160, 2, 1)}:
        raise ValueError("Unreviewed MMANA segmentation settings; use explicit JSON geometry instead")
    expect("***G/H/M/R/AzEl/X***")
    ground, height, material, impedance, view_az, view_el, extra = fields(7)
    if ground not in {"0", "1"}:
        raise ValueError("MAA soil settings are external to the file; specify them explicitly in JSON")
    if material != "0":
        raise ValueError(
            "MAA material presets are not physical conductivity; choose the JSON material model explicitly"
        )
    if float(height) != 0 or float(extra) != 0 or (float(view_az), float(view_el)) != (120, 60):
        raise ValueError("Unreviewed MAA height/auxiliary settings; cannot silently discard them")
    remaining = lines[cursor:]
    if remaining and remaining[0] == "###Comment###":
        remaining = []  # Original bytes are archived; this documented final section is annotation only.
    if any(remaining):
        raise ValueError("Unknown trailing MAA section")
    request = EngineeringRequest(
        name=name,
        frequency_hz=frequency,
        wires=tuple(wires),
        mesh=mesh,
        feed=Feed(
            wire=int(match[1]),
            position="center" if match[2] == "c" else "end",
            voltage_v=float(voltage),
            phase_deg=float(phase),
            resistance_ohm=resistance,
        ),
        material=Material(mode="ideal", basis="MAA explicitly selects no conductor loss (material 0)"),
        ground=Ground(mode="free_space" if ground == "0" else "ideal"),
        reference_impedance_ohm=float(impedance),
    )
    return ImportedMaa(request, segmentation, tuple(segments))


def export_maa(request: EngineeringRequest) -> str:
    """Export physical inputs for GAL comparison, not a claim of identical meshes."""
    if request.material.mode != "ideal" or request.ground.mode == "homogeneous_soil":
        raise ValueError("Physical conductivity/soil cannot be faithfully represented by this MAA export subset")
    source = f"w{request.feed.wire}{'c' if request.feed.position == 'center' else 'e'}"
    lines = [request.name, "*", f"{request.frequency_hz / 1e6:.17g}", "***Wires***", str(len(request.wires))]
    for wire in request.wires:
        lines.append(",".join(f"{value:.17g}" for value in (*wire.start_m, *wire.end_m, wire.radius_m)) + ",-1")
    lines.extend(["***Source***", "1,1", f"{source},{request.feed.phase_deg:.17g},{request.feed.voltage_v:.17g}"])
    lines.extend(["***Load***", "1,1" if request.feed.resistance_ohm else "0,1"])
    if request.feed.resistance_ohm:
        lines.append(f"{source},1,{request.feed.resistance_ohm:.17g},0")
    ground = 0 if request.ground.mode == "free_space" else 1
    lines.extend(
        [
            "***Segmentation***",
            "800,80,2,1",
            "***G/H/M/R/AzEl/X***",
            f"{ground},0,0,{request.reference_impedance_ohm:.17g},120,60,0",
            "###Comment###",
            "Reference geometry only. GAL automatic mesh differs from the saved pymininec mesh.",
            "Not a validated construction design or a claim of MMANA-GAL numerical compatibility.",
        ]
    )
    return "\r\n".join(lines) + "\r\n"
