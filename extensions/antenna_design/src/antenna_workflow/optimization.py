"""Bounded SciPy search over a fixed antenna topology, with solver evidence per candidate.

The optimizer chooses dimensions; pymininec computes performance. Each frequency
uses the existing project admission ledger and artifact format. No model calls
or population/checkpoint restoration are involved.
"""

from __future__ import annotations

import math
import os
import traceback
from importlib.metadata import version
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic
from typing import Annotated, Literal, Self

from pydantic import Field, FiniteFloat, StrictInt, model_validator

from .artifacts import complete_bundle, compute_bundle, fail_bundle, prepare_bundle, write_json
from .contracts import Contract, EngineeringRequest, InvalidCandidateError, Point, Positive, Wire
from .evaluation import EvaluationInput, EvaluationResult, FrequencyTarget, evaluate
from .mcp_resources import MAX_METADATA_BYTES, read_artifact, read_bounded, read_manifest, run_path
from .project_store import AdmissionError, ProjectStore, StatusInput

type StopReason = Literal[
    "in_progress",
    "sampled_targets_met",
    "budget_limit",
    "time_limit",
    "stagnation",
    "optimizer_finished",
    "failed",
    "interrupted",
]


class Dimension(Contract):
    wire: Annotated[StrictInt, Field(ge=1, le=16)]
    parameter: Literal["length_m", "center_x_m", "center_y_m", "center_z_m"]
    lower: FiniteFloat
    upper: FiniteFloat

    def initial(self, model: EngineeringRequest) -> float:
        wire = model.wires[self.wire - 1]
        if self.parameter == "length_m":
            return math.dist(wire.start_m, wire.end_m)
        axis = ("center_x_m", "center_y_m", "center_z_m").index(self.parameter)
        return (wire.start_m[axis] + wire.end_m[axis]) / 2


class OptimizationTargets(EvaluationInput):
    points: Annotated[tuple[FrequencyTarget, ...], Field(min_length=1, max_length=6)]


class OptimizeInput(Contract):
    run_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")]
    model: EngineeringRequest
    dimensions: Annotated[tuple[Dimension, ...], Field(min_length=1, max_length=24)]
    targets: OptimizationTargets
    max_solver_runs: Annotated[StrictInt | None, Field(ge=1, le=4096)] = None
    random_seed: Annotated[StrictInt, Field(ge=0, le=2**32 - 1)] = 0
    population_size: Annotated[StrictInt | None, Field(ge=5, le=256)] = None
    gain_scale_db: Positive = 1
    front_back_scale_db: Positive = 1
    swr_scale: Positive = 1
    minimum_generations: Annotated[StrictInt, Field(ge=1, le=100)] = 5
    stagnation_generations: Annotated[StrictInt, Field(ge=2, le=100)] = 5

    @model_validator(mode="after")
    def search_contract(self) -> Self:
        if self.targets.comparison_role != "design_objective":
            raise ValueError("Choose explicit design_objective targets; nominal references are not requirements")
        if self.targets.planned_new_solves is not None:
            raise ValueError("Use max_solver_runs for this search, not a separate planned_new_solves value")
        if self.targets.ground != self.model.ground:
            raise ValueError("Target and model ground conditions must agree")
        step = self.model.pattern.step_deg
        if self.targets.front_phi_deg % step or self.targets.elevation_deg % step:
            raise ValueError("Target directions must lie on the model angular grid")
        if self.max_solver_runs is not None and self.max_solver_runs < len(self.targets.points):
            raise ValueError("Budget must cover at least one complete frequency sweep")
        seeds = sum(point.run_id is not None for point in self.targets.points)
        if seeds not in (0, len(self.targets.points)):
            raise ValueError("Supply all baseline run_ids or none")
        keys = {(item.wire, item.parameter) for item in self.dimensions}
        if len(keys) != len(self.dimensions):
            raise ValueError("Each wire dimension may occur only once")
        endpoints = [point for wire in self.model.wires for point in (wire.start_m, wire.end_m)]
        if len(set(endpoints)) != len(endpoints) or self.model.feed.position != "center":
            raise ValueError("Search currently supports isolated elements with a center feed, not connected wires")
        for item in self.dimensions:
            if (
                item.wire > len(self.model.wires)
                or item.lower >= item.upper
                or not math.isfinite(item.upper - item.lower)
            ):
                raise ValueError("Dimension must select an existing wire and have increasing bounds")
            if not item.lower <= item.initial(self.model) <= item.upper:
                raise ValueError("The baseline dimension must be inside its search bounds")
            if item.parameter == "length_m" and item.lower <= 20 * self.model.wires[item.wire - 1].radius_m:
                raise ValueError("Length bounds must preserve the thin-wire requirement")
        return self


class Candidate(Contract):
    index: Annotated[int, Field(ge=0, le=4096)]
    parameters: Annotated[tuple[FiniteFloat, ...], Field(min_length=1, max_length=24)]
    score: Annotated[FiniteFloat, Field(ge=0)]
    run_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=6)]
    evaluation: EvaluationResult


class SearchBudget(Contract):
    """Search-local allowances; every actual solve still needs a project ledger admission."""

    hard_solver_runs: Annotated[int, Field(ge=1, le=4096)]
    validation_headroom: Annotated[int, Field(ge=0)]
    population_size: Annotated[int, Field(ge=5, le=256)]
    granted_candidates: Annotated[int, Field(ge=0, le=4096)] = 0
    batches: Annotated[int, Field(ge=0)] = 0
    stagnant_batches: Annotated[int, Field(ge=0)] = 0
    # Historical receipts did not measure generations; absence is not zero.
    generations_completed: Annotated[int | None, Field(ge=0)] = None
    stagnant_generations: Annotated[int | None, Field(ge=0)] = None
    estimated_candidate_seconds: Positive | None = None
    elapsed_seconds: Annotated[FiniteFloat, Field(ge=0)] = 0
    last_grant_reason: Literal["initial_exploration", "improvement", "bounded_exploration"] = "initial_exploration"


class OptimizationResult(Contract):
    schema_version: Literal[1] = 1
    run_id: str
    status: Literal["CANDIDATE_SEARCH_NOT_VALIDATED"] = "CANDIDATE_SEARCH_NOT_VALIDATED"
    stop_reason: StopReason
    algorithm: Literal["scipy.optimize.differential_evolution"] = "scipy.optimize.differential_evolution"
    scipy_version: str
    candidate_attempts: Annotated[int, Field(ge=0, le=4096)] = 0
    solver_admissions: Annotated[int, Field(ge=0)] = 0
    rejected_candidates: Annotated[int, Field(ge=0, le=4096)] = 0
    baseline_score: Annotated[FiniteFloat | None, Field(ge=0)] = None
    best: Candidate | None = None
    budget: SearchBudget | None = None
    llm_calls: Literal[0] = 0


def optimization_path(store: Path, run_id: str) -> Path:
    parent = store / ".optimizations"
    if parent.is_symlink() or parent.is_junction() or parent.resolve().parent != store:
        raise ValueError("Optimization directory must remain inside the project")
    return run_path(parent, run_id)


def _save(root: Path, result: OptimizationResult) -> None:
    # Readers see either the previous complete receipt or the new one; a killed
    # worker cannot leave a half-written best candidate as the visible result.
    pending: Path | None = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, prefix=".receipt-", delete=False) as stream:
            pending = Path(stream.name)
            stream.write(result.model_dump_json(indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(root / "progress.json")
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def candidate_model(query: OptimizeInput, values: tuple[float, ...]) -> EngineeringRequest:
    """Change only requested lengths/centers; direction, radius and topology stay fixed."""
    if len(values) != len(query.dimensions):
        raise ValueError("Parameter vector dimension mismatch")
    if any(not item.lower <= value <= item.upper for item, value in zip(query.dimensions, values, strict=True)):
        raise ValueError("Parameter vector is outside the agreed bounds")
    if values == tuple(item.initial(query.model) for item in query.dimensions):
        return query.model
    edits: dict[tuple[int, str], float] = {
        (item.wire, item.parameter): value for item, value in zip(query.dimensions, values, strict=True)
    }
    wires = []
    for index, wire in enumerate(query.model.wires, 1):
        if not any(item.wire == index for item in query.dimensions):
            wires.append(wire)
            continue
        length = math.dist(wire.start_m, wire.end_m)
        scale = edits.get((index, "length_m"), length) / length
        center = tuple(
            edits.get((index, f"center_{axis}_m"), (a + b) / 2)
            for axis, a, b in zip("xyz", wire.start_m, wire.end_m, strict=True)
        )
        half = tuple((b - a) * scale / 2 for a, b in zip(wire.start_m, wire.end_m, strict=True))
        start: Point = (center[0] - half[0], center[1] - half[1], center[2] - half[2])
        end: Point = (center[0] + half[0], center[1] + half[1], center[2] + half[2])
        wires.append(Wire(start_m=start, end_m=end, radius_m=wire.radius_m))
    endpoints = [point for wire in wires for point in (wire.start_m, wire.end_m)]
    if len(set(endpoints)) != len(endpoints):
        raise ValueError("A search candidate must not connect previously isolated elements")
    return EngineeringRequest.model_validate({**query.model.model_dump(), "wires": tuple(wires)})


def objective(query: OptimizeInput, evaluation: EvaluationResult) -> float | None:
    """Sum squared normalized shortfalls; surplus gain cannot cancel a bad F/B point."""
    shortfalls = []
    for point in evaluation.points:
        for metric, scale in (
            (point.forward_gain_dbi, query.gain_scale_db),
            (point.front_to_back_db, query.front_back_scale_db),
        ):
            if metric.margin is None:
                return None
            shortfalls.append(max(0, -metric.margin) / scale)
        if query.targets.max_direct_swr is not None:
            if point.direct_swr_at_system_impedance is None:
                return None
            shortfalls.append(
                max(0, point.direct_swr_at_system_impedance - query.targets.max_direct_swr) / query.swr_scale
            )
    score = sum(value * value for value in shortfalls)
    return score if math.isfinite(score) else None


def _targets_met(evaluation: EvaluationResult) -> bool:
    # Feasibility is checked from the actual comparisons, independently of
    # user-selected scoring scales and floating-point underflow in the score.
    return evaluation.radiation_status == "sampled_targets_met" and evaluation.matching_status in {
        "not_evaluated",
        "direct_targets_met",
    }


def _evaluate_candidate(
    store: Path, query: OptimizeInput, run_ids: tuple[str, ...], project: ProjectStore
) -> EvaluationResult:
    points = tuple(
        point.model_copy(update={"run_id": run_id}) for point, run_id in zip(query.targets.points, run_ids, strict=True)
    )
    return evaluate(store, query.targets.model_copy(update={"points": points}), project.snapshot(StatusInput(limit=1)))


def _check_baseline(store: Path, query: OptimizeInput, project: ProjectStore) -> Candidate | None:
    run_ids = tuple(point.run_id for point in query.targets.points if point.run_id is not None)
    if not run_ids:
        return None
    for run_id in run_ids:
        root = run_path(store, run_id)
        saved = EngineeringRequest.model_validate_json(read_artifact(root, read_manifest(root), "request"))
        physical = saved.model_dump(exclude={"name", "frequency_hz"})
        if physical != query.model.model_dump(exclude={"name", "frequency_hz"}):
            raise ValueError("Baseline evidence must describe the exact seed geometry, feed and numerical settings")
    evaluation = _evaluate_candidate(store, query, run_ids, project)
    score = objective(query, evaluation)
    if score is None:
        raise ValueError("Baseline evidence has unavailable target metrics")
    return Candidate(
        index=0,
        parameters=tuple(item.initial(query.model) for item in query.dimensions),
        score=score,
        run_ids=run_ids,
        evaluation=evaluation,
    )


def prepare_optimization(store: Path, query: OptimizeInput, project: ProjectStore) -> Path:
    if run_path(store, query.run_id).exists() or project.charged_search_attempts(query.run_id):
        raise AdmissionError("RUN_RESERVED", "Search identifier already belongs to prior evidence; do not replay it.")
    baseline = _check_baseline(store, query, project)
    remaining = project.snapshot(StatusInput(limit=1)).remaining_attempts
    if query.max_solver_runs is not None and query.max_solver_runs > remaining:
        raise AdmissionError("SOLVE_BUDGET_EXHAUSTED", "Requested search exceeds remaining project attempts.")
    frequencies = len(query.targets.points)
    # Automatic allocation leaves up to two sweeps for subsequent verification.
    # This is headroom, not a reservation against other clients of the project.
    reserve = min(2 * frequencies, max(0, remaining - frequencies)) if query.max_solver_runs is None else 0
    limit = min(4096, query.max_solver_runs if query.max_solver_runs is not None else remaining - reserve)
    if limit < frequencies:
        raise AdmissionError("SOLVE_BUDGET_EXHAUSTED", "Remaining attempts cannot cover one complete candidate.")
    budget = SearchBudget(
        hard_solver_runs=limit,
        validation_headroom=reserve,
        population_size=query.population_size or max(8, 2 * len(query.dimensions)),
        generations_completed=0,
        stagnant_generations=0,
    )
    root = optimization_path(store, query.run_id)
    root.parent.mkdir(exist_ok=True)
    root.mkdir()
    write_json(root / "request.json", query)
    _save(
        root,
        OptimizationResult(
            run_id=query.run_id,
            stop_reason="in_progress",
            scipy_version=version("scipy"),
            baseline_score=baseline.score if baseline else None,
            best=baseline,
            budget=budget,
        ),
    )
    return root


def get_optimization(store: Path, run_id: str, project: ProjectStore, *, active: bool) -> OptimizationResult:
    root = optimization_path(store, run_id)
    result = OptimizationResult.model_validate_json(read_bounded(root, "progress.json", MAX_METADATA_BYTES))
    if result.run_id != run_id:
        raise ValueError("Optimization identity mismatch")
    if result.best is not None:
        # A progress file is an index, not independent performance evidence.
        # Recheck published runs before exposing a saved score after restart.
        query = OptimizeInput.model_validate_json(read_bounded(root, "request.json", MAX_METADATA_BYTES))
        if query.run_id != run_id:
            raise ValueError("Optimization request identity mismatch")
        baseline_model = candidate_model(query, result.best.parameters)
        check = query.model_copy(
            update={
                "model": baseline_model,
                "targets": query.targets.model_copy(
                    update={
                        "points": tuple(
                            point.model_copy(update={"run_id": saved_id})
                            for point, saved_id in zip(query.targets.points, result.best.run_ids, strict=True)
                        )
                    }
                ),
            }
        )
        verified = _check_baseline(store, check, project)
        if verified is None or not math.isclose(verified.score, result.best.score, rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError("Saved objective disagrees with published numerical evidence")
        result = result.model_copy(update={"best": result.best.model_copy(update={"evaluation": verified.evaluation})})
    if result.stop_reason == "sampled_targets_met" and (
        result.best is None or not _targets_met(result.best.evaluation)
    ):
        raise ValueError("Target satisfaction requires a complete zero-shortfall candidate")
    if not active and result.stop_reason == "in_progress":
        result = result.model_copy(update={"stop_reason": "interrupted"})
    return result.model_copy(update={"solver_admissions": project.charged_search_attempts(run_id)})


class _SearchStoppedError(Exception):
    def __init__(self, reason: StopReason) -> None:
        self.reason = reason


def optimize(root: Path, *, timeout: int) -> None:
    """Run inside the single owned worker; hard process deadlines/cancellation belong to its parent."""
    import numpy as np
    from numpy.typing import NDArray
    from scipy.optimize import OptimizeResult, differential_evolution
    from scipy.stats import qmc

    started = monotonic()
    # Leave time for the final receipt and parent-side evidence verification.
    deadline = started + timeout - max(5, timeout * 0.1)
    query = OptimizeInput.model_validate_json(read_bounded(root, "request.json", MAX_METADATA_BYTES))
    # Refuse a second worker invocation even if a caller manually invokes the
    # hidden CLI entry; a saved receipt is not a resumable SciPy population.
    write_json(root / "worker-started.json", {"pid": os.getpid()})
    store = root.parent.parent
    project = ProjectStore(store)
    result = get_optimization(store, query.run_id, project, active=True)
    if result.budget is None:
        raise ValueError("Search has no allocation plan; saved receipts cannot restart a worker")
    budget: SearchBudget = result.budget
    initial = tuple(item.initial(query.model) for item in query.dimensions)
    cache: dict[tuple[float, ...], float] = {}
    if result.best is not None:
        cache[initial] = result.best.score
    points_count = len(query.targets.points)
    max_candidates = budget.hard_solver_runs // points_count
    batch_score = result.best.score if result.best else None
    generation_score: float | None = None
    # Unusable geometry/metrics are worse than every finite score. They never
    # become the saved best candidate or a claim of target satisfaction.
    rejected_score = float("inf")

    def cost(vector: NDArray[np.float64]) -> float:
        nonlocal result, budget, batch_score
        values = tuple(float(value) for value in vector)
        if values in cache:
            return cache[values]
        if (
            result.candidate_attempts >= max_candidates
            or result.solver_admissions + points_count > budget.hard_solver_runs
        ):
            raise _SearchStoppedError("budget_limit")
        time_left = deadline - monotonic()
        if time_left <= 0 or (
            budget.estimated_candidate_seconds is not None and time_left < budget.estimated_candidate_seconds
        ):
            raise _SearchStoppedError("time_limit")
        if result.candidate_attempts >= budget.granted_candidates:
            current_score = result.best.score if result.best else None
            improved = current_score is not None and (
                batch_score is None or batch_score - current_score > max(1e-8, abs(batch_score) * 0.001)
            )
            if budget.batches:
                write_json(
                    root / f"batch-{budget.batches:04d}.json",
                    {
                        "candidate_attempts": result.candidate_attempts,
                        "solver_admissions": result.solver_admissions,
                        "start_score": batch_score,
                        "end_score": current_score,
                        "improved": improved,
                        "elapsed_seconds": monotonic() - started,
                    },
                )
            size = min(budget.population_size, max_candidates - result.candidate_attempts)
            if budget.estimated_candidate_seconds is not None:
                size = min(size, int(time_left / budget.estimated_candidate_seconds))
            if size < 1:
                raise _SearchStoppedError("time_limit")
            budget = budget.model_copy(
                update={
                    "granted_candidates": result.candidate_attempts + size,
                    "batches": budget.batches + 1,
                    "last_grant_reason": (
                        "initial_exploration"
                        if not budget.batches
                        else "improvement"
                        if improved
                        else "bounded_exploration"
                    ),
                }
            )
            batch_score = current_score
        index = result.candidate_attempts + 1
        candidate_started = monotonic()
        result = result.model_copy(update={"candidate_attempts": index, "budget": budget})
        _save(root, result)
        run_ids = []
        satisfied = False
        try:
            try:
                model = candidate_model(query, values)
            except ValueError as error:
                raise InvalidCandidateError("Search geometry is outside the supported subset") from error
            for number, point in enumerate(query.targets.points, 1):
                run_id = f"{query.run_id}-c{index:04d}-f{number}"
                candidate = model.model_copy(update={"frequency_hz": point.frequency_hz})
                project.reserve_solve(run_id, candidate)
                result = result.model_copy(update={"solver_admissions": result.solver_admissions + 1})
                _save(root, result)
                destination = run_path(store, run_id)
                prepare_bundle(destination, candidate, timeout=timeout)
                try:
                    compute_bundle(destination, with_plots=False)
                    complete_bundle(destination)
                except BaseException as error:
                    fail_bundle(destination, error)
                    raise
                run_ids.append(run_id)
            evaluation = _evaluate_candidate(store, query, tuple(run_ids), project)
            score = objective(query, evaluation)
            satisfied = _targets_met(evaluation)
        except AdmissionError as error:
            if error.code != "SOLVE_BUDGET_EXHAUSTED":
                raise
            raise _SearchStoppedError("budget_limit") from None
        except InvalidCandidateError, np.linalg.LinAlgError:
            traceback.print_exc()  # Only the operator log receives numerical/input diagnostics.
            score = None
        cache[values] = rejected_score if score is None else score
        if score is None:
            result = result.model_copy(update={"rejected_candidates": result.rejected_candidates + 1})
        else:
            candidate_result = Candidate(
                index=index, parameters=values, score=score, run_ids=tuple(run_ids), evaluation=evaluation
            )
            write_json(root / f"candidate-{index:04d}.json", candidate_result)
            changes: dict[str, object] = {}
            if values == initial:
                changes["baseline_score"] = score
            if result.best is None or score < result.best.score or satisfied:
                changes["best"] = candidate_result
            result = result.model_copy(update=changes)
        if len(run_ids) == points_count:
            # Use a high-water estimate of complete sweeps, including I/O and
            # evaluation. A cheap invalid candidate must not lower the estimate.
            budget = budget.model_copy(
                update={
                    "estimated_candidate_seconds": max(
                        budget.estimated_candidate_seconds or 0, 1.25 * (monotonic() - candidate_started)
                    ),
                }
            )
        budget = budget.model_copy(update={"elapsed_seconds": monotonic() - started})
        result = result.model_copy(update={"budget": budget})
        _save(root, result)
        if satisfied:
            raise _SearchStoppedError("sampled_targets_met")
        return cache[values]

    def generation_finished(intermediate_result: OptimizeResult) -> None:
        nonlocal budget, result, generation_score
        # SciPy calls this after a complete generation, never during its initial
        # Latin-hypercube population. Resource batches are not search generations.
        current = float(intermediate_result.fun)
        improved = math.isfinite(current) and (
            generation_score is None or generation_score - current > max(1e-8, abs(generation_score) * 0.001)
        )
        completed = (budget.generations_completed or 0) + 1
        stagnant = 0 if improved else (budget.stagnant_generations or 0) + 1
        if improved:
            generation_score = current
        budget = budget.model_copy(update={"generations_completed": completed, "stagnant_generations": stagnant})
        result = result.model_copy(update={"budget": budget})
        write_json(
            root / f"generation-{completed:04d}.json",
            {
                "generation": completed,
                "candidate_attempts": result.candidate_attempts,
                "best_score": result.best.score if result.best else None,
                "stagnant_generations": stagnant,
            },
        )
        _save(root, result)
        if completed >= query.minimum_generations and stagnant >= query.stagnation_generations:
            raise _SearchStoppedError("stagnation")

    reason: StopReason = "failed"
    try:
        cost(np.asarray(initial, dtype=np.float64))
        if result.best is not None and _targets_met(result.best.evaluation):
            raise _SearchStoppedError("sampled_targets_met")
        bounds = [(item.lower, item.upper) for item in query.dimensions]
        population = qmc.scale(
            qmc.LatinHypercube(d=len(initial), rng=query.random_seed).random(n=budget.population_size),
            [item.lower for item in query.dimensions],
            [item.upper for item in query.dimensions],
        )
        differential_evolution(
            cost,
            bounds,
            init=population,
            x0=initial,
            rng=query.random_seed,
            maxiter=max_candidates,
            polish=False,
            workers=1,
            tol=0,
            atol=0,
            callback=generation_finished,
        )
        reason = "optimizer_finished"
    except _SearchStoppedError as stopped:
        reason = stopped.reason
    finally:
        budget = budget.model_copy(update={"elapsed_seconds": monotonic() - started})
        _save(root, result.model_copy(update={"stop_reason": reason, "budget": budget}))
