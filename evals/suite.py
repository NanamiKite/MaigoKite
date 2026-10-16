"""Inspect owns repetition, scoring, logs and the viewer; Core owns execution."""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path

from inspect_ai import Epochs, Task
from inspect_ai.dataset import Sample
from inspect_ai.log import transcript
from inspect_ai.model import ModelOutput
from inspect_ai.scorer import Score, Scorer, Target, mean, mean_score, pass_at, pass_k, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from langchain_core.language_models.chat_models import BaseChatModel

from .cases import Case, Observation, grade
from .driver import run_trial


@scorer(metrics=[mean()])
def outcome_score(cases: list[Case]) -> Scorer:
    by_id = {case.id: case for case in cases}

    async def score(state: TaskState, target: Target) -> Score:
        observation = Observation.model_validate(state.metadata["observation"])
        verdict = grade(by_id[str(state.sample_id)], observation)
        failed = [name for name, passed in verdict.checks.items() if not passed]
        return Score(
            value=int(verdict.passed), answer=observation.response,
            explanation="; ".join(failed) if failed else "All outcome and safety checks passed.",
            metadata={"checks": verdict.checks, "partial_credit": verdict.fraction},
        )

    return score


@solver
def product_solver(
    cases: list[Case], model_factory: Callable[[str], BaseChatModel], identity: str,
    timeout: int, max_calls: int, context_window: int,
) -> Solver:
    by_id = {case.id: case for case in cases}

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        case = by_id[str(state.sample_id)]
        result = await run_trial(case, model_factory(case.id), timeout=timeout,
                                 max_calls=max_calls, context_window=context_window,
                                 emit=lambda event: transcript().info(event, source="maigokite"))
        state.metadata["observation"] = result.model_dump(mode="json")
        # Product events contain tool requests/results, approvals, deltas and
        # provider usage. Preserve them rather than infer a trace from the answer.
        state.output = ModelOutput.from_content(identity, result.response)
        return state

    return solve


def personal_task(
    cases: list[Case], model_factory: Callable[[str], BaseChatModel], *, identity: str,
    evidence: str, epochs: int = 3, timeout: int = 90, max_calls: int = 8, context_window: int = 16384,
) -> Task:
    dataset_hash = hashlib.sha256(Path(__file__).with_name("personal.json").read_bytes()).hexdigest()
    return Task(
        name="personal_agent_" + evidence.lower(), version="1",
        dataset=[Sample(id=case.id, input="\n\n".join(case.prompts), metadata={"category": case.category})
                 for case in cases],
        solver=product_solver(cases, model_factory, identity, timeout, max_calls, context_window),
        scorer=outcome_score(cases),
        epochs=Epochs(epochs, [mean_score(), pass_at(epochs), pass_k(epochs)]),
        fail_on_error=False,
        metadata={
            "evidence": evidence, "agent_model": identity, "dataset_sha256": dataset_hash,
            "runtime": "AgentCore + LangChain create_agent + LangGraph",
            "versions": {name: version(name) for name in ("maigokite-agent", "inspect-ai", "langchain", "langgraph")},
            "scope": "isolated personal workspace; file_read/file_write + runtime write_todos; injected model boundary",
            "timeout_seconds": timeout, "max_model_calls_per_turn": max_calls, "context_window": context_window,
            "usage": "Read model.usage product events; absent counters are unknown, not zero.",
        },
    )
