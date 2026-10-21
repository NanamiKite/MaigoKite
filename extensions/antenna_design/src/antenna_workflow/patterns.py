"""Render sampled numerical cuts with matplotlib; never reconstruct fields from images."""

from __future__ import annotations

import math
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.projections.polar import PolarAxes

from .contracts import EngineeringRequest, FarField


def render(field: FarField, request: EngineeringRequest, directory: Path) -> None:
    """Write two polar cuts with absolute and common-peak-normalized scales.

    No angular interpolation or smoothing is used. The -40 dB display floor only
    affects plots; the raw JSON preserves solver floor markers and all sample values.
    """
    directory.mkdir(exist_ok=True)
    values = {(row.theta_deg, row.phi_deg): row.total_gain_dbi for row in field.samples}
    peak = field.normalized_reference_peak_dbi
    step = request.pattern.step_deg
    azimuth_angles = list(range(0, 360, step))
    azimuth_keys = [(90 - request.pattern.azimuth_cut_elevation_deg, angle) for angle in azimuth_angles]
    above_ground = request.ground.mode != "free_space"
    elevation_angles = list(range(0, 181 if above_ground else 360, step))
    front = request.pattern.elevation_cut_phi_deg
    elevation_keys = [
        (90 - angle, front) if angle <= 90 else (angle - 90, front + 180) if angle <= 270 else (450 - angle, front)
        for angle in elevation_angles
    ]
    cuts = (
        (
            "azimuth",
            azimuth_angles,
            azimuth_keys,
            f"elevation={request.pattern.azimuth_cut_elevation_deg} deg",
            ["+X", "+Y", "-X", "-Y"],
        ),
        (
            "elevation",
            elevation_angles,
            elevation_keys,
            f"vertical plane phi={front}/{front + 180} deg",
            [f"phi={front}", "+Z", f"phi={front + 180}", "-Z"],
        ),
    )
    for name, angles, keys, plane, labels in cuts:
        figure = Figure(figsize=(11, 5))
        FigureCanvasAgg(figure)
        for index, normalized in enumerate((False, True), start=1):
            axis = figure.add_subplot(1, 2, index, projection="polar")
            if not isinstance(axis, PolarAxes):
                raise TypeError("The requested matplotlib polar projection was not created")
            floor = -40.0 if normalized else peak - 40
            gains = [max(floor, values[key] - (peak if normalized else 0)) for key in keys]
            radians = [math.radians(angle) for angle in angles]
            closed = name == "azimuth" or not above_ground
            if closed:
                radians.append(2 * math.pi)
                gains.append(gains[0])
            axis.plot(radians, gains)
            axis.set_ylim(floor, 0 if normalized else peak)
            axis.set_xticks([0, math.pi / 2, math.pi, 1.5 * math.pi], labels)
            if not closed:
                axis.set_thetamax(180)
            cut_peak = max(values[key] for key in keys)
            if cut_peak != -999:
                cut_index = next(i for i, key in enumerate(keys) if values[key] == cut_peak)
                axis.plot(radians[cut_index], gains[cut_index], "o", label=f"Cut peak {cut_peak:.3f} dBi")
                axis.legend(loc="lower left", fontsize=7)
            if name == "azimuth" and request.pattern.front_phi_deg is not None:
                for offset, label in ((0, "Front"), (180, "Back")):
                    angle = math.radians((request.pattern.front_phi_deg + offset) % 360)
                    axis.plot([angle, angle], [floor, 0 if normalized else peak], "--", alpha=0.4)
                    axis.text(angle, 0 if normalized else peak, label, fontsize=8)
            axis.set_title("Normalized dB (full-grid peak = 0)" if normalized else "Absolute gain (dBi)")
        figure.suptitle(f"{request.frequency_hz / 1e6:g} MHz | {plane}\nFull-grid sampled peak {peak:.3f} dBi")
        figure.text(
            0.5, 0.015, "Display floor: peak - 40 dB. Sampled cuts, not a measured/validated design.", ha="center"
        )
        figure.tight_layout(rect=(0, 0.04, 1, 0.88))
        for extension in ("png", "svg"):
            with (directory / f"{name}.{extension}").open("xb") as stream:
                figure.savefig(stream, format=extension, dpi=140)
        figure.clear()
