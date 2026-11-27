"""Compact run summary from Inspect logs; the logs and viewer keep all evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from inspect_ai.log import EvalLog, EvalSample

from .cases import Case, Observation

OUTCOME = "outcome_score"
USAGE_COUNTERS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")


def _passed(sample: EvalSample) -> bool:
    score = (sample.scores or {}).get(OUTCOME)
    return sample.error is None and score is not None and score.value == 1


def _score_flags(sample: EvalSample) -> dict[str, object]:
    score = (sample.scores or {}).get(OUTCOME)
    metadata = score.metadata if score is not None else None
    return dict(metadata) if isinstance(metadata, dict) else {}


def _observation(sample: EvalSample) -> Observation | None:
    raw = sample.metadata.get("observation")
    return Observation.model_validate(raw) if isinstance(raw, dict) else None


def _failed_checks(sample: EvalSample) -> list[str]:
    checks = _score_flags(sample).get("checks")
    if not isinstance(checks, dict):
        return []
    return sorted(str(name) for name, passed in checks.items() if not passed)


def _provider_usage(observations: Sequence[Observation]) -> str:
    totals: Counter[str] = Counter()
    for observation in observations:
        for event in observation.events:
            payload = event.get("payload")
            if (event.get("type") != "model.usage" or not isinstance(payload, dict)
                    or payload.get("measurement") != "provider"):
                continue
            counters = payload.get("usage")
            if not isinstance(counters, dict):
                continue
            for name in USAGE_COUNTERS:
                value = counters.get(name)
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[name] += value
    if not totals:
        return "unknown (no provider model.usage events)"
    return ", ".join(f"{name}={totals[name]}" for name in USAGE_COUNTERS if name in totals)


def summary_lines(
    logs: Sequence[EvalLog], cases: Sequence[Case], *, epochs: int, auto_approve: bool,
) -> list[str]:
    """Per-reducer, per-category and forbidden-call figures computed from finished samples."""
    samples = [sample for log in logs for sample in (log.samples or [])]
    by_id = {case.id: case for case in cases}
    probes = {case.id for case in cases if case.forbidden_calls}
    lines: list[str] = []
    reducers = [
        f"{score.reducer}: " + ", ".join(f"{name}={metric.value:.3f}" for name, metric in score.metrics.items())
        for log in logs if log.results is not None for score in log.results.scores
    ]
    if reducers:
        lines.append(f"Reducers over {epochs} epochs: " + " | ".join(reducers))
    passed = sum(_passed(sample) for sample in samples)
    format_only = sum(
        sample.error is None and _failed_checks(sample) == ["answer"] for sample in samples
    )
    lines.append(
        f"Trials: {passed}/{len(samples)} passed; {len(samples) - passed} failed or errored "
        f"({format_only} of those passed every outcome and safety check and missed only the literal reply)"
    )

    observations = [observation for sample in samples if (observation := _observation(sample)) is not None]
    if observations:
        elapsed = [observation.elapsed_seconds for observation in observations]
        lines.append(
            f"Trials observed: {len(observations)}; elapsed mean {sum(elapsed) / len(elapsed):.2f}s, "
            f"max {max(elapsed):.2f}s; model calls {sum(observation.model_calls for observation in observations)}; "
            f"provider usage: {_provider_usage(observations)}"
        )

    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for sample in samples:
        case = by_id.get(str(sample.id))
        if case is not None:
            totals[case.category][1] += 1
            totals[case.category][0] += int(_passed(sample))
    if totals:
        lines.append("By category: " + " · ".join(
            f"{category} {passed_count}/{total}" for category, (passed_count, total) in sorted(totals.items())
        ))

    failures: dict[str, list[str]] = defaultdict(list)
    for sample in samples:
        if _passed(sample):
            continue
        failed = _failed_checks(sample)
        if sample.error is not None:
            reason = f"ERROR {sample.error.message}"
        elif failed == ["answer"]:
            score = (sample.scores or {}).get(OUTCOME)
            reason = "literal reply only"
            if score is not None:
                reason = f"literal reply only, got {str(score.answer)[:40]!r}"
        else:
            reason = failed and "; ".join(failed) or "no checks recorded"
        failures[str(sample.id)].append(f"epoch {sample.epoch}: {reason}")
    for case_id, reasons in sorted(failures.items()):
        lines.append(f"  FAIL {case_id}: " + " | ".join(reasons))

    if probes:
        attempted = executed = probe_trials = 0
        modes: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        for sample in samples:
            case = by_id.get(str(sample.id))
            if case is None or case.id not in probes:
                continue
            flags = _score_flags(sample)
            was_attempted = flags.get("attempted_unauthorized") is True
            was_executed = flags.get("executed_unauthorized") is True
            probe_trials += 1
            attempted += int(was_attempted)
            executed += int(was_executed)
            mode = modes[case.permission_mode]
            mode[0] += 1
            mode[1] += int(was_attempted)
            mode[2] += int(was_executed)
        lines.append(
            f"Forbidden-call probes ({len(probes)} tasks, {probe_trials} trials, safety layer "
            f"{'off' if auto_approve else 'on'}): attempted {attempted}, executed {executed}, "
            f"blocked {attempted - executed}"
        )
        lines.append("  by permission mode: " + " · ".join(
            f"{mode}: attempted {attempted_count}/{trial_count}, executed {executed_count}"
            for mode, (trial_count, attempted_count, executed_count) in sorted(modes.items())
        ))
    return lines
