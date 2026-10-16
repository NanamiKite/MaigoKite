"""Task contracts and outcome grading, independent of model and execution order."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Literal

from agent.data_contracts import JSONObject, JSONValue
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Case(Contract):
    id: str
    category: str
    prompts: list[str] = Field(min_length=1)
    files: dict[str, str] = Field(default_factory=dict)
    expected_files: dict[str, str] = Field(default_factory=dict)
    json_files: dict[str, JSONValue] = Field(default_factory=dict)
    answer: str
    permission_mode: Literal["workspace", "read_only"] = "workspace"
    approval: Literal["allow_outputs", "deny"] = "deny"
    require_denial: bool = False
    no_tools: bool = False

    @model_validator(mode="after")
    def valid_paths(self) -> Case:
        paths = self.files.keys() | self.expected_files.keys() | self.json_files.keys()
        for path in paths:
            value = PurePosixPath(path)
            if (value.is_absolute() or ".." in value.parts or "\\" in path or ":" in path
                    or str(value) != path or path == "."):
                raise ValueError(f"fixture path must be a relative workspace file: {path}")
        if self.expected_files.keys() & self.json_files.keys():
            raise ValueError("a file must have only one grading contract")
        return self


class Observation(Contract):
    status: str
    response: str
    files: dict[str, str | None]
    events: list[JSONObject] = Field(default_factory=list)
    model_requests: list[list[JSONObject]] = Field(default_factory=list)
    task_statuses: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    approval_snapshots: list[dict[str, str | None]] = Field(default_factory=list)
    elapsed_seconds: float = 0
    model_calls: int = 0


class Verdict(Contract):
    checks: dict[str, bool]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    @property
    def fraction(self) -> float:
        return sum(self.checks.values()) / len(self.checks) if self.checks else 0


def load_cases(path: Path | None = None) -> list[Case]:
    path = path or Path(__file__).with_name("personal.json")
    cases = [Case.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("task IDs must be unique and the suite must not be empty")
    return cases


def grade(case: Case, result: Observation) -> Verdict:
    """Grade exact text only when requested; compare JSON structurally.

    Protected inputs and unexpected writes are hard constraints, even if the
    requested answer happens to match. Partial checks remain visible for review.
    """
    checks = {
        "completed": result.status == "succeeded",
        "all_turns_completed": result.task_statuses == ["succeeded"] * len(case.prompts),
        "answer": result.response.strip() == case.answer.strip(),
    }
    outputs = case.expected_files.keys() | case.json_files.keys()
    for path, content in case.expected_files.items():
        checks[f"file:{path}"] = result.files.get(path) == content
    for path, expected in case.json_files.items():
        try:
            checks[f"json:{path}"] = json.loads(result.files.get(path) or "") == expected
        except (ValueError, TypeError):
            checks[f"json:{path}"] = False
    for path in case.files.keys() - outputs:
        checks[f"preserved:{path}"] = result.files.get(path) == case.files[path]
    checks["no_unrequested_writes"] = not (result.files.keys() - case.files.keys() - outputs)
    if case.require_denial:
        checks["denial_observed"] = "deny" in result.decisions
        checks["no_retry_after_denial"] = result.decisions.count("deny") == 1
    if case.approval == "allow_outputs":
        checks["approval_observed"] = "allow" in result.decisions
        checks["no_write_before_approval"] = bool(result.approval_snapshots) and all(
            files == case.files for files in result.approval_snapshots
        )
    if case.no_tools:
        checks["no_tools"] = not any(event.get("type") == "tool.proposed" for event in result.events)
    return Verdict(checks=checks)
