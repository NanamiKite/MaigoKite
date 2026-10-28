"""Export bounded candidate-design documents from immutable numerical evidence.

Publication is separate from solver runs and never grants engineering acceptance,
executes reproduction commands, or changes project admission accounting.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, JsonValue

from .artifacts import digest
from .assessment import AssessmentInput, DesignAssessment, assess_design
from .contracts import ArtifactRef, Contract, EngineeringRequest, EngineeringSummary
from .delivery_render import PresentationError, render_documents
from .evaluation import EvaluationError, EvaluationResult, MatchingStatus, RadiationStatus, evaluate
from .mcp_resources import (
    MAX_METADATA_BYTES,
    MAX_RESOURCE_BYTES,
    PUBLIC_ARTIFACTS,
    RUN_ID_PATTERN,
    ArtifactUnavailableError,
    Completion,
    read_artifact,
    read_bounded,
    read_manifest,
    run_path,
)
from .optimization import optimization_path
from .project_store import ProjectSnapshot

MAX_DELIVERY_BYTES = 48 * 1024 * 1024
DELIVERY_ARTIFACTS: dict[str, tuple[str, str]] = {
    "report": ("design-report.md", "text/markdown"),
    "geometry": ("geometry.svg", "image/svg+xml"),
    "dimensions": ("dimensions.csv", "text/csv"),
    "model": ("model.json", "application/json"),
    "materials": ("materials.json", "application/json"),
    "evaluation": ("evaluation.json", "application/json"),
    "validation": ("validation.json", "application/json"),
    "construction": ("construction.md", "text/markdown"),
    "reproduce": ("reproduce.json", "application/json"),
    "archive": ("design.zip", "application/zip"),
}
_RECORD = "delivery-record.json"
_BRIEF = "design_brief.json"
_PROOF_IDS = ("request", "summary", "far-field")
_REQUIRED_IDS = (*_PROOF_IDS, "compatibility", "currents")


class DeliveryInput(AssessmentInput):
    delivery_id: Annotated[str, Field(pattern=f"^{RUN_ID_PATTERN}$")]
    title: Annotated[str, Field(min_length=1, max_length=120)]
    purpose: Annotated[str, Field(min_length=1, max_length=1000)]
    assumptions: Annotated[tuple[Annotated[str, Field(max_length=500)], ...], Field(max_length=20)] = ()
    sources: Annotated[tuple[Annotated[str, Field(max_length=500)], ...], Field(max_length=20)] = ()


class ResourceReference(ArtifactRef):
    uri: str


class DeliveryResult(Contract):
    schema_version: Literal[1, 2] = 2
    kind: Literal["antenna_design_delivery"] = "antenna_design_delivery"
    delivery_id: Annotated[str, Field(pattern=f"^{RUN_ID_PATTERN}$")]
    reused: bool
    status: Literal["CANDIDATE_NOT_VALIDATED"] = "CANDIDATE_NOT_VALIDATED"
    design_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")]
    physical_design_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    simulation_configurations: dict[str, str] = Field(default_factory=dict)
    resources: Annotated[tuple[ResourceReference, ...], Field(min_length=10, max_length=10)]
    radiation_status: RadiationStatus
    matching_status: MatchingStatus | None = None
    next_actions: Annotated[tuple[str, ...], Field(max_length=20)] = ()


class DeliveryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _DeliveryRecord(Contract):
    schema_version: Literal[1, 2] = 2
    request: DeliveryInput
    source_manifests: dict[str, str]
    design_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")]
    physical_design_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    simulation_configurations: dict[str, str] = Field(default_factory=dict)
    radiation_status: RadiationStatus
    matching_status: MatchingStatus | None = None
    next_actions: Annotated[tuple[str, ...], Field(max_length=20)]


@dataclass
class _Evidence:
    files: dict[str, bytes] = field(default_factory=dict)
    manifests: dict[str, str] = field(default_factory=dict)
    refs: dict[str, dict[str, ArtifactRef]] = field(default_factory=dict)
    models: dict[str, EngineeringRequest] = field(default_factory=dict)
    summaries: dict[str, EngineeringSummary] = field(default_factory=dict)
    missing: dict[str, list[str]] = field(default_factory=dict)
    remaining_read_bytes: int = MAX_DELIVERY_BYTES

    def charge(self, size: int) -> None:
        if size < 0 or size > self.remaining_read_bytes:
            raise DeliveryError("DELIVERY_TOO_LARGE", "Delivery evidence exceeds the aggregate read limit.")
        self.remaining_read_bytes -= size


def _json(value: JsonValue | Contract) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, Contract) else value
    return (json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ref(name: str, data: bytes, media_type: str) -> ArtifactRef:
    return ArtifactRef(path=name, media_type=media_type, size_bytes=len(data), sha256=_sha(data))


def _manifest_hash(manifest: dict[str, ArtifactRef]) -> str:
    return _sha(_json({name: ref.model_dump(mode="json") for name, ref in manifest.items()}))


def _location(store: Path, delivery_id: str, *, create: bool = False) -> Path:
    if re.fullmatch(RUN_ID_PATTERN, delivery_id) is None:
        raise DeliveryError("DELIVERY_INVALID_ID", "Invalid delivery identifier.")
    if store.is_symlink() or store.is_junction() or not store.is_dir():
        raise DeliveryError("DELIVERY_UNAVAILABLE", "The evidence store is not an unlinked directory.")
    store = store.resolve()
    parent = store / ".deliveries"
    if parent.is_symlink() or parent.is_junction() or parent.resolve().parent != store:
        raise DeliveryError("DELIVERY_UNAVAILABLE", "The delivery store must be an unlinked direct child.")
    if create:
        parent.mkdir(exist_ok=True)
    if parent.exists() and not parent.is_dir():
        raise DeliveryError("DELIVERY_UNAVAILABLE", "The delivery store is not a directory.")
    return run_path(parent, delivery_id)


def _read_published(root: Path, manifest: dict[str, ArtifactRef], name: str, limit: int) -> bytes:
    ref = manifest.get(name)
    if ref is None or ref.path != name or not 0 <= ref.size_bytes <= limit:
        raise ArtifactUnavailableError("Missing or oversized delivery artifact")
    data = read_bounded(root, name, limit)
    if len(data) != ref.size_bytes or _sha(data) != ref.sha256:
        raise ArtifactUnavailableError("Delivery artifact digest mismatch")
    return data


def _load_delivery(root: Path) -> tuple[_DeliveryRecord, dict[str, ArtifactRef]]:
    if not root.exists():
        raise DeliveryError("DELIVERY_NOT_FOUND", "The requested delivery does not exist.")
    if not (root / "completed.json").exists():
        raise DeliveryError("DELIVERY_INCOMPLETE", "A partial delivery is retained; use a new delivery identifier.")
    manifest = read_manifest(root)
    expected = {_RECORD, _BRIEF, *(name for name, _ in DELIVERY_ARTIFACTS.values())}
    if (
        set(manifest) != expected
        or any(not 0 <= ref.size_bytes <= MAX_RESOURCE_BYTES for ref in manifest.values())
        or sum(ref.size_bytes for ref in manifest.values()) > MAX_DELIVERY_BYTES - 3 * MAX_METADATA_BYTES
    ):
        raise ArtifactUnavailableError("Invalid delivery manifest")
    record = _DeliveryRecord.model_validate_json(_read_published(root, manifest, _RECORD, MAX_METADATA_BYTES))
    if record.request.delivery_id != root.name:
        raise ArtifactUnavailableError("Delivery identity mismatch")
    for name, mime in DELIVERY_ARTIFACTS.values():
        ref = manifest[name]
        if ref.path != name or ref.media_type != mime or not 0 <= ref.size_bytes <= MAX_RESOURCE_BYTES:
            raise ArtifactUnavailableError("Invalid delivery reference")
    return record, manifest


def _result(record: _DeliveryRecord, manifest: dict[str, ArtifactRef], *, reused: bool) -> DeliveryResult:
    delivery_id = record.request.delivery_id
    return DeliveryResult(
        schema_version=record.schema_version,
        delivery_id=delivery_id,
        reused=reused,
        design_sha256=record.design_sha256,
        physical_design_sha256=record.physical_design_sha256,
        simulation_configurations=record.simulation_configurations,
        resources=tuple(
            ResourceReference(
                **manifest[name].model_dump(),
                uri=f"antenna://deliveries/{delivery_id}/{artifact_id}",
            )
            for artifact_id, (name, _) in DELIVERY_ARTIFACTS.items()
        ),
        radiation_status=record.radiation_status,
        matching_status=record.matching_status,
        next_actions=record.next_actions,
    )


def get_delivery(store: Path, delivery_id: str) -> DeliveryResult:
    """Retrieve a published immutable candidate; do not consult or change project budgets."""
    try:
        root = _location(store, delivery_id)
        record, manifest = _load_delivery(root)
        # A successful retrieval must not advertise absent or modified resources.
        for name in manifest:
            _read_published(root, manifest, name, MAX_RESOURCE_BYTES)
        return _result(record, manifest, reused=True)
    except DeliveryError:
        raise
    except (OSError, ValueError, KeyError) as error:
        raise DeliveryError(
            "DELIVERY_UNAVAILABLE", "Delivery evidence is unavailable or failed integrity checks."
        ) from error


def get_delivery_input(store: Path, delivery_id: str) -> DeliveryInput:
    """Read the manifest-bound input for workflow reconciliation, without reevaluating a design."""
    record, _ = _load_delivery(_location(store, delivery_id))
    return record.request


def read_delivery_resource(store: Path, delivery_id: str, artifact_id: str) -> tuple[bytes, str]:
    """Read a fixed published resource, never a user-supplied path or ZIP member."""
    if artifact_id not in DELIVERY_ARTIFACTS:
        raise DeliveryError("DELIVERY_UNKNOWN_RESOURCE", "Unknown delivery resource identifier.")
    try:
        root = _location(store, delivery_id)
        _, manifest = _load_delivery(root)
        name, mime = DELIVERY_ARTIFACTS[artifact_id]
        return _read_published(root, manifest, name, MAX_RESOURCE_BYTES), mime
    except DeliveryError:
        raise
    except (OSError, ValueError, KeyError) as error:
        raise DeliveryError(
            "DELIVERY_UNAVAILABLE", "Delivery evidence is unavailable or failed integrity checks."
        ) from error


def _collect(store: Path, request: DeliveryInput, *, read_limit: int = MAX_DELIVERY_BYTES) -> _Evidence:
    evidence = _Evidence(remaining_read_bytes=read_limit)
    for point in request.evaluation.points:
        run_id = point.run_id
        assert run_id is not None
        root = run_path(store, run_id)
        # Reserve metadata reads for this pass, evaluation and the post-evaluation check.
        evidence.charge(3 * (MAX_METADATA_BYTES + 1024))
        manifest = read_manifest(root)
        if any(PUBLIC_ARTIFACTS[key][0] not in manifest for key in _REQUIRED_IDS):
            raise ArtifactUnavailableError("Required numerical evidence is not published")
        evidence.manifests[run_id] = _manifest_hash(manifest)
        evidence.refs[run_id] = manifest
        evidence.missing[run_id] = []
        for artifact_id, (name, _) in PUBLIC_ARTIFACTS.items():
            if name not in manifest:
                evidence.missing[run_id].append(name)
                continue
            limit = MAX_METADATA_BYTES if artifact_id in {"request", "summary", "compatibility"} else MAX_RESOURCE_BYTES
            # Evaluation reads these three files again. Reserve those bytes before
            # reading other resources, keeping the complete export operation bounded.
            ref = manifest[name]
            if ref.path != name or not 0 <= ref.size_bytes <= limit:
                raise ArtifactUnavailableError("Invalid evidence reference")
            if artifact_id in _PROOF_IDS:
                evidence.charge(ref.size_bytes)
            allowed = min(limit, evidence.remaining_read_bytes)
            data = (
                read_artifact(root, manifest, artifact_id)
                if allowed == limit
                else _read_published(root, manifest, name, allowed)
            )
            evidence.charge(len(data))
            evidence.files[f"evidence/{run_id}/{name}"] = data
        if "environment.json" in manifest:
            data = _read_published(
                root, manifest, "environment.json", min(MAX_METADATA_BYTES, evidence.remaining_read_bytes)
            )
            # Preserve the original verified metadata, not arbitrary files named by it.
            if not isinstance(json.loads(data), dict):
                raise ArtifactUnavailableError("Historical environment must be an object")
            evidence.charge(len(data))
            evidence.files[f"evidence/{run_id}/environment.json"] = data
        else:
            evidence.missing[run_id].append("environment.json")
        prefix = f"evidence/{run_id}/"
        evidence.models[run_id] = EngineeringRequest.model_validate_json(evidence.files[prefix + "request.json"])
        evidence.summaries[run_id] = EngineeringSummary.model_validate_json(evidence.files[prefix + "simulation.json"])
    return evidence


def _check_evaluation(store: Path, evidence: _Evidence, evaluation: EvaluationResult) -> None:
    for point in evaluation.points:
        if point.run_id is None or point.evidence is None:
            raise ArtifactUnavailableError("Missing evaluated evidence")
        manifest = evidence.refs[point.run_id]
        expected = (
            manifest["request.json"].sha256,
            manifest["simulation.json"].sha256,
            manifest["pattern/far_field.json"].sha256,
        )
        actual = (point.evidence.request_sha256, point.evidence.summary_sha256, point.evidence.far_field_sha256)
        current = read_manifest(run_path(store, point.run_id))
        if actual != expected or _manifest_hash(current) != evidence.manifests[point.run_id]:
            raise ArtifactUnavailableError("Evidence changed while preparing delivery")


def _collect_workflow(store: Path, request: DeliveryInput, evidence: _Evidence, assessment: DesignAssessment) -> None:
    """Archive exactly the assessed evidence and recheck source digests before publication."""
    if request.optimization_run_id is not None:
        root = optimization_path(store, request.optimization_run_id)
        assert assessment.optimization_evidence is not None
        for name, data in zip(("request.json", "result.json"), assessment.optimization_evidence, strict=True):
            evidence.charge(len(data))
            evidence.files[f"optimization/{name}"] = data
        for name in ("design-plan.json", "design-request.json"):
            if (root / name).exists():
                data = read_bounded(root, name, MAX_METADATA_BYTES)
                evidence.charge(len(data))
                evidence.files[f"optimization/{name}"] = data
        for path in sorted(root.iterdir()):
            if re.fullmatch(r"(?:batch|generation)-[0-9]{4}\.json", path.name):
                data = read_bounded(root, path.name, min(MAX_METADATA_BYTES, evidence.remaining_read_bytes))
                evidence.charge(len(data))
                evidence.files[f"optimization/{path.name}"] = data
    if assessment.verification is not None:
        data = _json(assessment.verification)
        evidence.charge(len(data))
        evidence.files["verification/mesh.json"] = data
    if assessment.matching is not None:
        data = _json(assessment.matching)
        evidence.charge(len(data))
        evidence.files["matching/l-networks.json"] = data
    for number, (check, (fixed, evaluated)) in enumerate(
        zip(request.matching_checks, assessment.matching_checks, strict=True), 1
    ):
        for name, value in (("request", check), ("result", fixed), ("radiation", evaluated)):
            data = _json(value)
            evidence.charge(len(data))
            evidence.files[f"matching/check-{number}/{name}.json"] = data
    for evaluated in assessment.additional_evaluations:
        allowance = evidence.remaining_read_bytes
        extra = _collect(store, request.model_copy(update={"evaluation": evaluated.targets}), read_limit=allowance)
        _check_evaluation(store, extra, evaluated)
        evidence.charge(allowance - extra.remaining_read_bytes)
        for name, data in extra.files.items():
            if name not in evidence.files:
                evidence.files[name] = data


def _mime(name: str) -> str:
    for relative, mime in (*DELIVERY_ARTIFACTS.values(), *PUBLIC_ARTIFACTS.values()):
        if name == relative or name.endswith("/" + relative):
            return mime
    return "application/json"


def _archive(documents: dict[str, bytes], evidence: _Evidence) -> bytes:
    contents = {**documents, **evidence.files}
    manifest: dict[str, JsonValue] = {
        name: _ref(name, data, _mime(name)).model_dump(mode="json") for name, data in contents.items()
    }
    contents["manifest.json"] = _json(manifest)
    if any(len(data) > MAX_RESOURCE_BYTES for data in contents.values()):
        raise DeliveryError("DELIVERY_TOO_LARGE", "A delivery member exceeds the per-file size limit.")
    if sum(len(data) for data in contents.values()) > MAX_DELIVERY_BYTES:
        raise DeliveryError("DELIVERY_TOO_LARGE", "The uncompressed delivery exceeds the package size limit.")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=False) as archive:
        for name, data in contents.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
            if stream.tell() > MAX_RESOURCE_BYTES:
                raise DeliveryError("DELIVERY_TOO_LARGE", "The compressed delivery exceeds the resource size limit.")
    result = stream.getvalue()
    if len(result) > MAX_RESOURCE_BYTES:
        raise DeliveryError("DELIVERY_TOO_LARGE", "The compressed delivery exceeds the resource size limit.")
    return result


def _publish(root: Path, record: _DeliveryRecord, documents: dict[str, bytes]) -> DeliveryResult:
    documents[_RECORD] = _json(record)
    if len(documents[_RECORD]) > MAX_METADATA_BYTES:
        raise DeliveryError("DELIVERY_TOO_LARGE", "Delivery metadata exceeds its size limit.")
    if any(len(data) > MAX_RESOURCE_BYTES for data in documents.values()):
        raise DeliveryError("DELIVERY_TOO_LARGE", "A delivery resource exceeds its size limit.")
    if sum(len(data) for data in documents.values()) > MAX_DELIVERY_BYTES - 3 * MAX_METADATA_BYTES:
        raise DeliveryError("DELIVERY_TOO_LARGE", "The published delivery exceeds its aggregate size limit.")
    root.mkdir(exist_ok=False)
    manifest: dict[str, ArtifactRef] = {}
    for name, data in documents.items():
        with (root / name).open("xb") as stream:
            stream.write(data)
        manifest[name] = _ref(name, data, _mime(name))
    manifest_data = _json({name: ref.model_dump(mode="json") for name, ref in manifest.items()})
    if len(manifest_data) > MAX_METADATA_BYTES:
        raise DeliveryError("DELIVERY_TOO_LARGE", "The delivery manifest exceeds its size limit.")
    with (root / "manifest.pending").open("xb") as stream:
        stream.write(manifest_data)
    (root / "manifest.pending").rename(root / "manifest.json")
    receipt = Completion(manifest_sha256=digest(root / "manifest.json"))
    with (root / "completed.pending").open("xb") as stream:
        stream.write(_json(receipt))
    # The completion receipt is the final publication boundary. Partial directories
    # are intentionally retained and never overwritten or counted as solver runs.
    (root / "completed.pending").rename(root / "completed.json")
    return _result(record, manifest, reused=False)


def export_delivery(store: Path, request: DeliveryInput, snapshot: ProjectSnapshot) -> DeliveryResult:
    """Build a candidate package using saved evidence only; never solve, reserve or call a model."""
    try:
        root = _location(store, request.delivery_id)
        existing: _DeliveryRecord | None = None
        if root.exists():
            existing, manifest = _load_delivery(root)
            if existing.request != request:
                raise DeliveryError(
                    "DELIVERY_INPUT_CONFLICT", "The delivery identifier is already bound to another input."
                )
            metadata_allowance = (len(request.evaluation.points) + 4) * (MAX_METADATA_BYTES + 1024)
            if sum(ref.size_bytes for ref in manifest.values()) + metadata_allowance > MAX_DELIVERY_BYTES:
                raise DeliveryError("DELIVERY_TOO_LARGE", "Repeated export exceeds the aggregate read limit.")
        store = store.resolve()
        if existing is not None:
            # The retained ZIP owns its verified evidence copies. On reuse compare
            # completed source identities, then verify the published package, rather
            # than reading every source and the same complete package a second time.
            identities: dict[str, str] = {}
            for point in request.evaluation.points:
                assert point.run_id is not None
                identities[point.run_id] = _manifest_hash(read_manifest(run_path(store, point.run_id)))
            if existing.source_manifests != identities:
                raise DeliveryError(
                    "DELIVERY_INPUT_CONFLICT", "The delivery identifier is bound to different source evidence."
                )
            return get_delivery(store, request.delivery_id)
        evidence = _collect(store, request)
        evaluation = evaluate(store, request.evaluation, snapshot)
        _check_evaluation(store, evidence, evaluation)
        assessment = assess_design(store, request, evaluation, snapshot)
        _collect_workflow(store, request, evidence, assessment)
        documents = render_documents(request, evaluation, evidence, assessment)
        documents["design.zip"] = _archive(documents, evidence)
        record = _DeliveryRecord(
            request=request,
            source_manifests=evidence.manifests,
            design_sha256=evaluation.design_sha256,
            physical_design_sha256=evaluation.physical_design_sha256,
            simulation_configurations=evaluation.simulation_configurations,
            radiation_status=assessment.radiation_status,
            matching_status=assessment.matching_status,
            next_actions=assessment.next_actions,
        )
        root = _location(store, request.delivery_id, create=True)
        return _publish(root, record, documents)
    except DeliveryError:
        raise
    except PresentationError as error:
        raise DeliveryError(error.code, str(error)) from error
    except EvaluationError as error:
        raise DeliveryError(
            "DELIVERY_EVALUATION_FAILED", "Selected evidence cannot be evaluated on the stated basis."
        ) from error
    except (OSError, ValueError, KeyError, OverflowError, RecursionError) as error:
        raise DeliveryError(
            "DELIVERY_UNAVAILABLE", "Delivery evidence is unavailable or failed integrity checks."
        ) from error
