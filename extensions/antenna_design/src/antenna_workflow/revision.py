"""Prepare explicit frequency and installation changes without spending solve admissions."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, FiniteFloat, model_validator

from .contracts import Contract, EngineeringRequest, Material
from .evaluation import EvaluationInput
from .mcp_resources import RUN_ID_PATTERN, read_artifact, read_manifest, run_path


class Installation(Contract):
    feed_height_m: Annotated[FiniteFloat, Field(ge=0, le=1000)]
    rotation_xyz_deg: tuple[
        Annotated[FiniteFloat, Field(ge=-360, le=360)],
        Annotated[FiniteFloat, Field(ge=-360, le=360)],
        Annotated[FiniteFloat, Field(ge=-360, le=360)],
    ] = (0, 0, 0)
    notes: Annotated[str, Field(max_length=1000)] = ""


class RevisionInput(Contract):
    source_run_id: Annotated[str, Field(pattern=f"^{RUN_ID_PATTERN}$")]
    targets: EvaluationInput
    installation: Installation | None = None
    material: Material | None = None

    @model_validator(mode="after")
    def new_samples(self) -> Self:
        if any(point.run_id is not None for point in self.targets.points):
            raise ValueError("Revised frequencies/installation require new solves; do not reuse old run_ids")
        return self


class PreparedRevision(Contract):
    status: Literal["PREPARED_NOT_SIMULATED"] = "PREPARED_NOT_SIMULATED"
    source_run_id: str
    source_request_sha256: str
    model: EngineeringRequest
    targets: EvaluationInput
    installation: Installation | None
    assumptions: tuple[str, ...]
    solver_runs_this_call: Literal[0] = 0
    admissions_reserved: Literal[0] = 0


def prepare_revision(store: Path, query: RevisionInput) -> PreparedRevision:
    root = run_path(store, query.source_run_id)
    manifest = read_manifest(root)
    source = EngineeringRequest.model_validate_json(read_artifact(root, manifest, "request"))
    if query.targets.ground != source.ground and query.installation is None:
        raise ValueError("Changing the ground model requires an explicit installation/feed height")
    data = source.model_dump(mode="json")
    data["frequency_hz"] = query.targets.points[0].frequency_hz
    data["ground"] = query.targets.ground.model_dump(mode="json")
    data["reference_impedance_ohm"] = query.targets.system_impedance_ohm
    data["pattern"]["front_phi_deg"] = query.targets.front_phi_deg
    data["pattern"]["azimuth_cut_elevation_deg"] = query.targets.elevation_deg
    if query.material is not None:
        data["material"] = query.material.model_dump(mode="json")
    if query.installation is not None:
        import numpy as np
        from scipy.spatial.transform import Rotation

        origin = np.asarray(source.feed_position())
        destination = np.asarray((origin[0], origin[1], query.installation.feed_height_m))
        rotation = Rotation.from_euler("xyz", query.installation.rotation_xyz_deg, degrees=True)
        # Rotate around the original feed, then place that feed at the requested
        # height. Do not silently shift an element to clear the ground afterward.
        for wire in data["wires"]:
            for endpoint in ("start_m", "end_m"):
                wire[endpoint] = (rotation.apply(np.asarray(wire[endpoint]) - origin) + destination).tolist()
    model = EngineeringRequest.model_validate(data)
    assumptions: tuple[str, ...] = (
        "Frequency targets are explicit samples, not a continuous bandwidth acceptance.",
        "Installation models rigid geometry and the stated ground only; buildings, mast, roof, "
        "supports, feedline, wind and power ratings are not simulated.",
        "Rotation uses extrinsic X/Y/Z angles about the source feed; front direction is an absolute target angle.",
    )
    if query.installation is not None and query.installation.notes:
        assumptions += ("Unmodeled installation note: " + query.installation.notes,)
    return PreparedRevision(
        source_run_id=query.source_run_id,
        source_request_sha256=manifest["request.json"].sha256,
        model=model,
        targets=query.targets,
        installation=query.installation,
        assumptions=assumptions,
    )
