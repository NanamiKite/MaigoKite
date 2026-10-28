"""Render candidate documents from an already assessed design; no solver or acceptance decisions."""

from __future__ import annotations

import csv
import html
import io
import json
import math
from typing import TYPE_CHECKING

from pydantic import JsonValue

from .assessment import DesignAssessment
from .contracts import Contract, EngineeringRequest, Point
from .evaluation import EvaluationResult

if TYPE_CHECKING:
    from .delivery import DeliveryInput, _Evidence


class PresentationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_UNVERIFIED = (
    "bandwidth_and_between_sample_performance",
    "mesh_convergence",
    "matching_network_and_feedline",
    "actual_material_alloy_wall_thickness_and_joints",
    "boom_mast_insulators_fasteners_and_wind_load",
    "power_rating_and_electrical_clearances",
    "manufacturing_tolerances_and_cut_allowances",
    "installation_ground_and_measurements",
    "independent_mmana_import_and_validation",
)


def _json(value: JsonValue | Contract) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, Contract) else value
    return (json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode()


def _dimensions(model: EngineeringRequest) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(
        ("wire_index", "start_x_m", "start_y_m", "start_z_m", "end_x_m", "end_y_m", "end_z_m", "length_m", "diameter_m")
    )
    for index, wire in enumerate(model.wires, 1):
        values = (*wire.start_m, *wire.end_m, math.dist(wire.start_m, wire.end_m), 2 * wire.radius_m)
        if not all(math.isfinite(value) for value in values):
            raise PresentationError("DELIVERY_GEOMETRY_UNAVAILABLE", "Model dimensions exceed finite drawing limits.")
        writer.writerow((index, *(format(value, ".17g") for value in values)))
    return text.getvalue().encode()


def _geometry(model: EngineeringRequest) -> bytes:
    points = [point for wire in model.wires for point in (wire.start_m, wire.end_m)]
    feed = model.feed_position()
    if not all(math.isfinite(value) for value in feed):
        raise PresentationError("DELIVERY_GEOMETRY_UNAVAILABLE", "Model feed exceeds finite drawing limits.")
    rows = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="860" viewBox="0 0 1200 860">',
        '<rect width="1200" height="860" fill="white"/>',
        '<g font-family="sans-serif" fill="#172638">',
        '<text x="32" y="32" font-size="20">Candidate numerical geometry — NOT fabrication approval</text>',
        '<text x="32" y="58" font-size="14">Coordinates, lengths and diameters: metres (m). '
        "Exact endpoints: dimensions.csv.</text>",
        '<text x="32" y="80" font-size="14">Wire numbering follows model.json; '
        "a projection may overlap distinct wires.</text>",
    ]
    for second_axis, offset_y in ((1, 110), (2, 440)):
        mins = (min(point[0] for point in points), min(point[second_axis] for point in points))
        spans = (
            max(point[0] for point in points) - mins[0],
            max(point[second_axis] for point in points) - mins[1],
        )
        if not all(math.isfinite(value) for value in spans):
            raise PresentationError("DELIVERY_GEOMETRY_UNAVAILABLE", "Model extents exceed finite drawing limits.")
        scale = min(1000 / max(spans[0], 1e-9), 240 / max(spans[1], 1e-9))
        xpad, ypad = (1000 - spans[0] * scale) / 2, (240 - spans[1] * scale) / 2

        def project(
            point: Point,
            origin: tuple[float, float] = mins,
            factor: float = scale,
            axis: int = second_axis,
            x_padding: float = xpad,
            y_padding: float = ypad,
            y_offset: int = offset_y,
        ) -> tuple[float, float]:
            return (
                100 + x_padding + (point[0] - origin[0]) * factor,
                y_offset + 270 - y_padding - (point[axis] - origin[1]) * factor,
            )

        label = "Y" if second_axis == 1 else "Z"
        rows.append(f'<text x="32" y="{offset_y + 15}" font-size="18">X{label} projection</text>')
        rows.append(f'<rect x="80" y="{offset_y + 25}" width="1040" height="260" fill="none" stroke="#b9c4cf"/>')
        rows.append(f'<text x="1050" y="{offset_y + 309}" font-size="14">+X (m) →</text>')
        rows.append(f'<text x="8" y="{offset_y + 150}" font-size="14">+{label} ↑</text>')
        for index, wire in enumerate(model.wires, 1):
            x1, y1 = project(wire.start_m)
            x2, y2 = project(wire.end_m)
            length = math.dist(wire.start_m, wire.end_m)
            detail = html.escape(
                f"W{index}: start={wire.start_m} m; end={wire.end_m} m; length={length:.9g} m; "
                f"diameter={2 * wire.radius_m:.9g} m"
            )
            rows.append(
                f'<g><title>{detail}</title><line x1="{x1:.3f}" y1="{y1:.3f}" '
                f'x2="{x2:.3f}" y2="{y2:.3f}" stroke="#165d9c" stroke-width="3"/>'
                f'<text x="{x2 + 5:.3f}" y="{y2 - 5:.3f}" font-size="12">W{index}, L={length:.6g} m</text></g>'
            )
        fx, fy = project(feed)
        rows.append(f'<circle cx="{fx:.3f}" cy="{fy:.3f}" r="6" fill="#b32932"/>')
        rows.append(f'<text x="{fx + 9:.3f}" y="{fy + 18:.3f}" font-size="13">Feed W{model.feed.wire}</text>')
        rows.append(
            f'<text x="100" y="{offset_y + 309}" font-size="12">'
            f"X [{mins[0]:.9g}, {mins[0] + spans[0]:.9g}] m; "
            f"{label} [{mins[1]:.9g}, {mins[1] + spans[1]:.9g}] m</text>"
        )
    rows.extend(
        (
            '<text x="32" y="805" font-size="14">Numerical centre lines only; '
            "boom, feed gap, joints and supports are unspecified.</text>",
            '<text x="32" y="830" font-size="14">No cut allowance, mechanical tolerance '
            "or load/power rating is implied.</text>",
            "</g></svg>\n",
        )
    )
    return "\n".join(rows).encode()


def _number(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.9g}"


def _quoted(value: str) -> str:
    # Markdown quotation escapes HTML; caller notes remain unverified input.
    return "\n".join("> " + html.escape(line).replace("`", "&#96;") for line in value.splitlines())


def _report(
    request: DeliveryInput, evaluation: EvaluationResult, evidence: _Evidence, assessment: DesignAssessment
) -> bytes:
    lines = [
        "# Candidate antenna design report",
        "",
        _quoted(request.title),
        "",
        "Status: CANDIDATE_NOT_VALIDATED. This is an inspectable design candidate, not fabrication acceptance.",
        "",
        "## Stated purpose",
        "",
        _quoted(request.purpose),
        "",
        f"Physical design identity: `{evaluation.physical_design_sha256}`.",
        f"Acceptance criteria: `{evaluation.criteria_sha256}`.",
        "Per-run simulation identities and historical legacy hashes are recorded in evaluation.json.",
        f"Base-grid radiation comparison: `{evaluation.radiation_status}`; "
        f"target role: `{request.evaluation.comparison_role}`.",
        f"Radiation including any supplied mesh checks: `{assessment.radiation_status}`.",
        f"Base-grid matching comparison: `{evaluation.matching_status}`; "
        f"feed strategy: `{request.evaluation.matching}`.",
        f"Matching including any supplied mesh checks: `{assessment.matching_status}`.",
        "",
        f"Forward direction: phi={request.evaluation.front_phi_deg}°, elevation={request.evaluation.elevation_deg}°; "
        "F/B uses the same elevation and the opposite azimuth. Gain is dBi; F/B and margins are dB.",
        "The peak column is the sampled global peak, not the directional gain used to compare the objective.",
        "Not requested means that metric does not participate in acceptance or optimization scoring.",
        "",
        "| MHz | Native R + jX (ohm) | Forward / target / margin | F/B / target / margin | Peak dBi | Direct SWR |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for point in evaluation.points:
        assert point.run_id is not None and point.model_input_impedance_ohm is not None
        impedance = point.model_input_impedance_ohm
        gain, fb = point.forward_gain_dbi, point.front_to_back_db
        gain_target = _number(gain.minimum) if gain.minimum is not None else "not requested"
        fb_target = _number(fb.minimum) if fb.minimum is not None else "not requested"
        peak = evidence.summaries[point.run_id].sampled_peak_gain_dbi
        lines.append(
            f"| {point.frequency_hz / 1e6:.9g} | {impedance.r:.9g} + j({impedance.x:.9g}) | "
            f"{_number(gain.actual)} / {gain_target} / {_number(gain.margin)} | "
            f"{_number(fb.actual)} / {fb_target} / {_number(fb.margin)} | {peak:.9g} | "
            f"{_number(point.direct_swr_at_system_impedance)} ({point.direct_swr_status}) |"
        )
    lines.extend(
        (
            "",
            "Direct SWR is recalculated from native model impedance at "
            f"{request.evaluation.system_impedance_ohm:.9g} ohm.",
            "It is not SWR after an unspecified transformer, matching network, balun or feedline.",
            "A centre-frequency sample does not establish a usable band or band-edge compliance.",
            "Reference targets are comparisons only; a negative margin is not a software defect.",
            "",
            "## Validation gaps",
            "",
            *(f"- {item}" for item in _UNVERIFIED),
            "",
            "## Next actions",
            "",
            *(f"- {action}" for action in assessment.next_actions),
            "- Resolve the engineering gaps in validation.json before treating model dimensions as fabrication data.",
            "",
            "## Budget snapshot and proposal",
            "",
            f"Recorded limit {evaluation.budget.solve_limit}; admitted attempts {evaluation.budget.reserved_attempts}; "
            f"remaining {evaluation.budget.remaining_attempts}.",
            f"Proposed new solves {evaluation.budget.proposed_new_solves}; "
            f"additional total-limit allowance needed {evaluation.budget.additional_attempts_needed}.",
            "This is a saved, non-binding snapshot: export executed zero solves and reserved zero admissions.",
            "Reproduction commands are not permission to bypass the existing project budget.",
            "",
            "## Source availability",
            "",
        )
    )
    for run_id, missing in evidence.missing.items():
        availability = "missing " + ", ".join(missing) if missing else "all public artifacts and environment saved"
        lines.append(f"- {run_id}: {availability}")
    if assessment.optimization is not None:
        search = assessment.optimization
        lines.extend(
            (
                "",
                "## Numerical search",
                "",
                f"Stop reason: `{search.stop_reason}`.",
                f"Candidates: {search.candidate_attempts}; solve admissions: {search.solver_admissions}.",
                "Objective: sampled feasibility. Minimize squared normalized threshold violations; "
                "do not maximize surplus gain after every requested constraint is satisfied.",
                "Search stopping is not engineering acceptance. Full bounds, budget and seed: optimization/.",
            )
        )
    if assessment.verification is not None:
        lines.extend(
            (
                "",
                "## Mesh sensitivity",
                "",
                f"Status: `{assessment.verification.status}`.",
                "Declared tolerances, all comparisons and source runs are in verification/mesh.json.",
                "This covers sampled mesh sensitivity only, not bandwidth or fabrication certification.",
            )
        )
        levels = [assessment.verification.deltas[0].from_segments]
        levels.extend(delta.to_segments for delta in assessment.verification.deltas)
        lines.extend(
            ("", "| Segments/wire | Sampled radiation targets | Direct-feed SWR targets |", "| --- | --- | --- |")
        )
        for level, checked in zip(levels, assessment.verification.evaluations, strict=True):
            lines.append(f"| {level} | {checked.radiation_status} | {checked.matching_status} |")
    if assessment.matching is not None:
        lines.extend(
            (
                "",
                "## Ideal per-frequency matching candidates",
                "",
                "Computed with SciPy/scikit-rf from the native impedance at the antenna feed reference plane.",
                "Each row is a separate candidate. Different frequencies require switching or retuning; "
                "these are not one fixed multiband circuit. Components are ideal and unrounded.",
                "Source-shunt: source → (shunt to return) → series → antenna. "
                "Load-shunt: source → series → (shunt across antenna). Return is the circuit reference, "
                "not an instruction to ground a balanced antenna element.",
                "A balun/common-mode solution, component Q/loss/tolerances and power ratings remain unverified.",
                "",
                "| MHz | Topology (source to antenna) | Series | Shunt | Ideal matched SWR |",
                "| --- | --- | --- | --- | --- |",
            )
        )
        for sample in assessment.matching.points:
            if not sample.solutions:
                lines.append(f"| {sample.frequency_hz / 1e6:.9g} | no_solution_found | — | — | — |")
            for solution in sample.solutions:
                series, shunt = solution.components
                lines.append(
                    f"| {sample.frequency_hz / 1e6:.9g} | {solution.topology} | "
                    f"{series.kind} {series.value_si:.9g} {series.unit} | "
                    f"{shunt.kind} {shunt.value_si:.9g} {shunt.unit} | {solution.swr:.9g} |"
                )
        lines.extend(("", "Full precision parameters and source run IDs: matching/l-networks.json in the ZIP."))
    if assessment.matching_checks:
        lines.extend(
            (
                "",
                "## Fixed-component sampled matching checks",
                "",
                "Within each check, the same ideal components are used at every saved frequency without retuning. "
                "Separate checks are alternative networks, not a single automatically switched multiband circuit.",
                "No SWR target is inferred. These samples do not certify the gaps between frequencies or "
                "component losses, Q, tolerances, power ratings, balun or feedline behavior.",
                "Neighbor radiation comparisons remain in matching/check-N/radiation.json; "
                "a good input SWR does not change the bare-antenna gain or F/B result.",
                "",
                "| Check | Topology | Series | Shunt | MHz | Fixed-network SWR | Sample target status |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            )
        )
        for number, (check, _) in enumerate(assessment.matching_checks, 1):
            series, shunt = check.network.components
            for matched_point in check.points:
                lines.append(
                    f"| {number} | {check.network.topology} | "
                    f"{series.kind} {series.value_si:.9g} {series.unit} | "
                    f"{shunt.kind} {shunt.value_si:.9g} {shunt.unit} | "
                    f"{matched_point.frequency_hz / 1e6:.9g} | {matched_point.swr:.9g} | {check.target_status} |"
                )
        lines.extend(("", "Exact input, circuit results and numerical source evidence are retained in the ZIP."))
        lines.extend(
            (
                "",
                "### Radiation at the matching-check frequencies",
                "",
                "These comparisons use each check's stated reference/target role; they do not create new "
                "operating band requirements. A below-reference value must not be hidden by a good SWR.",
                "",
                "| Check | Role | MHz | Gain dBi | Gain margin dB | F/B dB | F/B margin dB |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            )
        )
        for number, (_, radiation) in enumerate(assessment.matching_checks, 1):
            for radiation_point in radiation.points:
                lines.append(
                    f"| {number} | {radiation.targets.comparison_role} | {radiation_point.frequency_hz / 1e6:.9g} | "
                    f"{_number(radiation_point.forward_gain_dbi.actual)} | "
                    f"{_number(radiation_point.forward_gain_dbi.margin)} | "
                    f"{_number(radiation_point.front_to_back_db.actual)} | "
                    f"{_number(radiation_point.front_to_back_db.margin)} |"
                )
    lines.extend(("", "## Caller-supplied assumptions (not verified)", ""))
    lines.extend(_quoted(item) for item in request.assumptions)
    lines.extend(("", "## Caller-supplied source notes (not fetched or verified)", ""))
    lines.extend(_quoted(item) for item in request.sources)
    return ("\n".join(lines) + "\n").encode()


def _construction(model: EngineeringRequest, request: DeliveryInput) -> bytes:
    endpoints: dict[Point, list[str]] = {}
    for index, wire in enumerate(model.wires, 1):
        endpoints.setdefault(wire.start_m, []).append(f"W{index}.start")
        endpoints.setdefault(wire.end_m, []).append(f"W{index}.end")
    junctions = [f"- {', '.join(labels)} at {point} m" for point, labels in endpoints.items() if len(labels) > 1]
    frequencies = ", ".join(f"{point.frequency_hz / 1e6:.9g} MHz" for point in request.evaluation.points)
    lines = [
        "# Candidate construction and adjustment notes",
        "",
        "CANDIDATE_NOT_VALIDATED. These are model-specific planning notes, not a released build procedure.",
        "",
        "## Geometry and connections actually modeled",
        "",
        f"The model contains {len(model.wires)} numbered thin wires. "
        "Use dimensions.csv for every start/end coordinate, "
        "centre-line length and diameter in metres; do not reinterpret a wire index as a proven element function.",
        "Use one right-handed XYZ datum for both projections. Phi runs from +X toward +Y; theta is measured from +Z.",
        "Lengths are numerical centre-line dimensions, not tube blank lengths, feed-gap lengths or cutting allowances.",
        "Shared endpoints represent the following numerical electrical connections:",
        "",
        *(junctions or ["- No endpoints are shared; each modeled wire is electrically separate from the other wires."]),
        "",
        f"The source is W{model.feed.wire}, position={model.feed.position}, coordinate={model.feed_position()} m; "
        f"voltage={model.feed.voltage_v:.9g} V, phase={model.feed.phase_deg:.9g}°, "
        f"source resistance={model.feed.resistance_ohm:.9g} ohm in the numerical model.",
        "These source parameters do not specify a connector, physical feed gap, "
        "balun, matching circuit or power rating.",
        f"Material model: {model.material.mode}. Ground model: {model.ground.mode}.",
        "",
        "## Decisions needed before fabrication",
        "",
        "Select and document actual alloy/conductivity, tube wall thickness, joints, "
        "insulating supports and corrosion control.",
        "Design the boom/mast/clamps, tolerances, electrical clearances and wind/power ratings independently; "
        "none are modeled.",
        "Do not connect numerically separate wires through a conductive support "
        "without revising and re-evaluating the model.",
        "An ideal-material simulation does not specify an actual metal or certify conductor losses.",
        "Resolve direct-feed versus a separate matching network using native R+jX; "
        "do not infer a transformer ratio from SWR.",
        "",
        "## Proposed adjustment and measurement sequence (not performed)",
        "",
        "1. Freeze the coordinate datum, exact dimensions and physical material/support/feed decisions "
        "in a new revision.",
        "2. Re-evaluate that revision at every objective frequency and both band edges; "
        "check mesh convergence separately.",
        "3. If a matching network is selected, document its circuit, losses and reference planes "
        "and evaluate it explicitly.",
        f"4. With a calibrated analyser at a documented feed reference plane, record R+jX and SWR at {frequencies} "
        "and across the intended bands; document feedline/common-mode control and the actual installed environment.",
        "5. Compare measured values with the complete modeled feed chain. "
        "Change one documented geometric parameter at a time; any changed wire length or position "
        "creates a new revision requiring all target frequencies to be checked again.",
        "6. Verify radiation, F/B, mechanical safety and operating-power suitability independently before release.",
        "",
        "No verified trimming increments, physical matching parts, acceptable fabrication tolerances "
        "or acceptance test results "
        "exist in this candidate package. Unknown values must be supplied and validated, not inferred from the plots.",
    ]
    return ("\n".join(lines) + "\n").encode()


def render_documents(
    request: DeliveryInput, evaluation: EvaluationResult, evidence: _Evidence, assessment: DesignAssessment
) -> dict[str, bytes]:
    model = next(iter(evidence.models.values()))
    material_rows: list[JsonValue] = [
        {
            "wire_index": index,
            "length_m": math.dist(wire.start_m, wire.end_m),
            "diameter_m": 2 * wire.radius_m,
            "modeled_wire_quantity": 1,
        }
        for index, wire in enumerate(model.wires, 1)
    ]
    reproduction: list[JsonValue] = []
    for run_id, source in evidence.models.items():
        prefix = f"evidence/{run_id}/"
        has_environment = prefix + "environment.json" in evidence.files
        reproduction.append(
            {
                "run_id": run_id,
                "frequency_hz": source.frequency_hz,
                "request": prefix + "request.json",
                "environment": prefix + "environment.json" if has_environment else None,
                "historical_environment_status": "recorded_not_recreated" if has_environment else "missing",
                "full_historical_reproduction_verified": False,
                "argv": [
                    "antenna-design",
                    "solve",
                    prefix + "request.json",
                    "--output",
                    f"recomputed/{run_id}",
                    "--timeout",
                    "120",
                ],
                "source_manifest_canonical_sha256": evidence.manifests[run_id],
                "copied_artifacts": [
                    ref.model_dump(mode="json")
                    for name, ref in evidence.refs[run_id].items()
                    if prefix + name in evidence.files
                ],
            }
        )
    return {
        "design-report.md": _report(request, evaluation, evidence, assessment),
        "geometry.svg": _geometry(model),
        "dimensions.csv": _dimensions(model),
        "model.json": _json(model),
        "materials.json": _json(
            {
                "schema_version": 1,
                "status": "MODEL_MATERIALS_NOT_A_PURCHASING_BOM",
                "material": model.material.model_dump(mode="json"),
                "items": material_rows,
                "actual_alloy": None,
                "tube_wall_thickness_m": None,
                "boom_supports_and_matching_parts": None,
                "note": "Ideal material is not an alloy choice; conductivity alone does not specify an actual alloy.",
            }
        ),
        "evaluation.json": _json(evaluation),
        "validation.json": _json(
            {
                "schema_version": 1,
                "status": "CANDIDATE_NOT_VALIDATED",
                "design_sha256": evaluation.design_sha256,
                "physical_design_sha256": evaluation.physical_design_sha256,
                "simulation_configurations": {
                    run_id: digest for run_id, digest in evaluation.simulation_configurations.items()
                },
                "criteria_sha256": evaluation.criteria_sha256,
                "verified_for_export": [
                    "completed_source_manifests_and_copied_artifact_digests",
                    "same_nonfrequency_design_across_selected_runs",
                    "target_frequency_ground_and_direction_sample_consistency",
                    "saved_native_impedance_reference_swr_and_directional_metrics",
                ],
                "not_verified": list(_UNVERIFIED),
                "missing_source_artifacts": {run_id: list(missing) for run_id, missing in evidence.missing.items()},
                "fabrication_approved": False,
                "measurements_performed_by_export": False,
                "solver_runs_this_call": 0,
                "admissions_reserved": 0,
                "mesh_sensitivity_status": assessment.verification.status if assessment.verification else "not_run",
                "radiation_status_including_mesh_checks": assessment.radiation_status,
                "matching_status_including_mesh_checks": assessment.matching_status,
                "search_stop_reason": assessment.optimization.stop_reason if assessment.optimization else None,
                "matching_parameters_status": assessment.matching.status if assessment.matching else "not_computed",
                "fixed_matching_checks": [
                    {"status": check.status, "target_status": check.target_status}
                    for check, _ in assessment.matching_checks
                ],
            }
        ),
        "construction.md": _construction(model, request),
        "reproduce.json": _json(
            {
                "schema_version": 1,
                "working_directory": "extracted_delivery_root",
                "coordinate_unit": "m",
                "frequency_unit": "Hz",
                "runs": reproduction,
                "optimization": "optimization/request.json" if assessment.optimization else None,
                "optimization_result": "optimization/result.json" if assessment.optimization else None,
                "mesh_verification": "verification/mesh.json" if assessment.verification else None,
                "matching_parameters": "matching/l-networks.json" if assessment.matching else None,
                "fixed_matching_checks": [
                    f"matching/check-{number}/request.json" for number in range(1, len(assessment.matching_checks) + 1)
                ],
                "matching_options": request.matching_options.model_dump(mode="json")
                if request.matching_options is not None
                else None,
                "instructions": [
                    "Install an approved antenna-design environment; historical metadata is evidence, not a lock.",
                    "Create the recomputed parent directory and use new outputs; original evidence is immutable.",
                    "Commands are argv arrays for deliberate offline execution; this export executes no commands.",
                    "Obtain required existing-project solve admission before recomputation; export grants no budget.",
                    "Missing environment metadata prevents historical reconstruction; reproduction is uncertified.",
                    "MAA and plots, when present, are copied originals; MMANA interoperability is not newly verified.",
                ],
            }
        ),
        "design_brief.json": _json(
            {
                "schema_version": 1,
                "delivery_id": request.delivery_id,
                "title": request.title,
                "purpose": request.purpose,
                "assumptions": list(request.assumptions),
                "sources": list(request.sources),
                "caller_notes_verified": False,
                "status": "CANDIDATE_NOT_VALIDATED",
                "design_sha256": evaluation.design_sha256,
                "physical_design_sha256": evaluation.physical_design_sha256,
                "simulation_configurations": {
                    run_id: digest for run_id, digest in evaluation.simulation_configurations.items()
                },
                "criteria_sha256": evaluation.criteria_sha256,
                "targets": request.evaluation.model_dump(mode="json"),
                "model_json_frequency_is_representative_only": True,
            }
        ),
    }
