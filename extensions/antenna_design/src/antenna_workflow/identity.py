"""Versioned input identities; numerical provenance remains in each run's environment artifact."""

from __future__ import annotations

import hashlib
import json

from pydantic import JsonValue

from .contracts import Contract, EngineeringRequest


def fingerprint(namespace: str, value: JsonValue) -> str:
    encoded = json.dumps([namespace, value], sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


class DesignIdentity(Contract):
    physical_design_sha256: str
    simulation_configuration_sha256: str
    sweep_basis_sha256: str
    mesh_comparison_basis_sha256: str
    legacy_design_sha256: str


def identify(model: EngineeringRequest) -> DesignIdentity:
    """Separate the built conductor from its excitation, environment and discretization.

    Wire order is significant because the feed addresses a wire by index. Material
    rationale and display names are not physical properties. Execution limits are
    retained in request.json, not in the identity of the requested computation.
    """
    physical: dict[str, JsonValue] = {
        "wires": [wire.model_dump(mode="json") for wire in model.wires],
        "feed": model.feed.model_dump(mode="json", exclude={"voltage_v", "phase_deg"}),
        "material": model.material.model_dump(mode="json", exclude={"basis"}),
    }
    configuration: dict[str, JsonValue] = {
        "frequency_hz": model.frequency_hz,
        "ground": model.ground.model_dump(mode="json"),
        "mesh": model.mesh.model_dump(mode="json"),
        "pattern": model.pattern.model_dump(mode="json"),
        "excitation": {"voltage_v": model.feed.voltage_v, "phase_deg": model.feed.phase_deg},
        "reference_impedance_ohm": model.reference_impedance_ohm,
    }
    sweep = {key: value for key, value in configuration.items() if key != "frequency_hz"}
    mesh_basis: dict[str, JsonValue] = {**sweep, "mesh": {"mode": model.mesh.mode}}
    legacy = model.model_dump(mode="json", exclude={"name", "frequency_hz"})
    return DesignIdentity(
        physical_design_sha256=fingerprint("physical-design-v1", physical),
        simulation_configuration_sha256=fingerprint("simulation-configuration-v1", configuration),
        sweep_basis_sha256=fingerprint("sweep-basis-v1", sweep),
        mesh_comparison_basis_sha256=fingerprint("mesh-comparison-basis-v1", mesh_basis),
        legacy_design_sha256=hashlib.sha256(
            json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )
