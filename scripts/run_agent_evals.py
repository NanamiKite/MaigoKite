"""Explicit, credential-isolated entry point for local Inspect evaluations."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("reference", "live"), default="reference")
    parser.add_argument("--provider", choices=("openai", "openai_compatible", "anthropic", "ollama"))
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--allow-remote", action="store_true", help="Explicitly permit a potentially paid remote API")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--max-calls", type=int, default=8)
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    parser.add_argument("--context-window", type=int, default=16384)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--log-dir", default=str(ROOT / "test-results" / "agent-evals"))
    options = parser.parse_args(argv)
    if not (1 <= options.epochs <= 20 and 1 <= options.max_calls <= 20 and 1 <= options.timeout <= 600
            and 1 <= options.max_output_tokens <= 8192 and options.context_window >= 4096):
        parser.error("invalid trial/time/model-call/token limits")
    if options.mode == "live":
        if not all((options.provider, options.model, options.base_url)):
            parser.error("live requires explicit --provider, --model and --base-url")
        endpoint = urlsplit(options.base_url)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
            parser.error("base URL must be HTTP(S), with no credentials, query or fragment")
        if endpoint.hostname not in {"127.0.0.1", "localhost", "::1"} and not options.allow_remote:
            parser.error("non-loopback endpoint requires --allow-remote (may incur charges)")
        if options.provider in {"openai", "anthropic"} and not os.getenv("MAIGOKITE_EVAL_API_KEY"):
            parser.error("set a dedicated MAIGOKITE_EVAL_API_KEY; normal application keys are never used")
    elif any((options.provider, options.model, options.base_url, options.allow_remote)):
        parser.error("provider options require --mode live")
    return options


def isolate_environment() -> str:
    """Keep OS essentials, discard provider/tracing/product/Inspect configuration.

    This entry point owns its process. It never changes the caller's environment
    or reads .env/YAML/keyring. Only the dedicated eval key is returned in memory.
    """
    key = os.environ.pop("MAIGOKITE_EVAL_API_KEY", "")
    essentials = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR",
                  "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL", "VIRTUAL_ENV",
                  "SSL_CERT_FILE", "SSL_CERT_DIR"}
    for name in list(os.environ):
        if name.upper() not in essentials:
            del os.environ[name]
    os.environ.update({"PYTHON_DOTENV_DISABLED": "1", "LANGSMITH_TRACING": "false",
                       "LANGCHAIN_TRACING_V2": "false", "PYTHONDONTWRITEBYTECODE": "1"})
    sys.dont_write_bytecode = True
    return key


def deny_network(event: str, args: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise PermissionError("reference evaluations prohibit network access")


def main() -> int:
    options = arguments()
    key = isolate_environment()
    if options.mode == "reference":
        sys.addaudithook(deny_network)
    with TemporaryDirectory(prefix="maigokite-eval-support-") as support:
        # Inspect's platformdirs caches belong to this run, not the user's
        # application directories. Reports have their own explicit destination.
        for variable in ("XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA"):
            os.environ[variable] = str(Path(support) / variable.lower())
        return evaluate(options, key)


def evaluate(options: argparse.Namespace, key: str) -> int:
    sys.path[:0] = [str(ROOT), str(ROOT / "python")]
    # Imports happen after tracing, implicit dotenv and ambient keys are disabled.
    from agent.models.native_model import build_chat_model
    from inspect_ai import eval as inspect_eval

    from evals.cases import load_cases
    from evals.reference_model import reference_model
    from evals.suite import personal_task

    cases = load_cases()
    if options.cases:
        unknown = set(options.cases) - {case.id for case in cases}
        if unknown:
            raise SystemExit(f"Unknown cases: {sorted(unknown)}")
        cases = [case for case in cases if case.id in options.cases]
    identity = "scripted-reference" if options.mode == "reference" else f"{options.provider}/{options.model}"
    factory = reference_model if options.mode == "reference" else lambda _case_id: build_chat_model(
        provider=options.provider, model=options.model, base_url=options.base_url,
        api_key=key or "local-eval-no-key", max_tokens=options.max_output_tokens, timeout=options.timeout,
    )
    evidence = "HARNESS_ONLY" if options.mode == "reference" else "LIVE"
    task = personal_task(cases, factory, identity=identity, evidence=evidence, epochs=options.epochs,
                         timeout=options.timeout, max_calls=options.max_calls, context_window=options.context_window)
    task.metadata = {**(task.metadata or {}), "max_output_tokens": options.max_output_tokens,
                     "endpoint": options.base_url, "temperature": "provider factory default"}
    print(f"Evidence: {evidence}; agent: {identity}; {len(cases)} tasks x {options.epochs} trials")
    # The Inspect transport placeholder never generates. Our solver calls the
    # real product model; agent_model/evidence in metadata identify what ran.
    logs = inspect_eval(task, model="mockllm/unused", log_dir=options.log_dir, display="plain",
                        max_samples=1, retry_on_error=0, log_format="json", log_model_api=False,
                        log_shared=False)
    samples = [sample for log in logs for sample in (log.samples or [])]
    errors = sum(sample.error is not None for sample in samples)
    passed = sum(sample.error is None and bool(sample.scores)
                 and all(score.value == 1 for score in (sample.scores or {}).values()) for sample in samples)
    complete = len(samples) == len(cases) * options.epochs and all(log.status == "success" for log in logs)
    print(f"{evidence}: {passed}/{len(samples)} passed; {errors} errors; complete={complete}")
    print(f"Inspect reports: {options.log_dir}")
    return 0 if complete and passed == len(samples) else 1


if __name__ == "__main__":
    raise SystemExit(main())
