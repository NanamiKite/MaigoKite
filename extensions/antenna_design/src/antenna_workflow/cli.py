"""One owned solver process per CLI invocation; no Agent host or GUI dependency."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import ValidationError

from .artifacts import complete_bundle, fail_bundle, prepare_bundle, worker_command, worker_environment
from .contracts import EngineeringRequest, EngineeringSummary, Mesh
from .mmana import read_maa

MAX_INPUT_BYTES = 65536


def _request(path: Path, mesh_name: str | None, encoding: str) -> tuple[EngineeringRequest, bytes]:
    with path.open("rb") as stream:
        original = stream.read(MAX_INPUT_BYTES + 1)
    if len(original) > MAX_INPUT_BYTES:
        raise ValueError("Input exceeds the 64 KiB single-model limit")
    if path.suffix.lower() == ".maa":
        if mesh_name is None:
            raise ValueError("MAA requires --mesh uniform80 or --mesh taper32; GAL segmentation is not copied")
        mode: Literal["uniform", "both_end_taper"] = "uniform" if mesh_name == "uniform80" else "both_end_taper"
        mesh = Mesh(mode=mode, segments_per_wire=80 if mesh_name == "uniform80" else 32)
        return read_maa(original.decode(encoding), mesh).request, original
    if path.suffix.lower() != ".json" or mesh_name is not None:
        raise ValueError("Use .json with its own mesh settings, or .maa with an explicit --mesh")
    return EngineeringRequest.model_validate_json(original), original


def _worker(root: Path) -> int:
    from .artifacts import compute_bundle

    try:
        compute_bundle(root)
    except Exception:
        traceback.print_exc()  # Worker output is a local diagnostic artifact, never a model tool response.
        return 1
    return 0


def run(input_path: Path, output: Path, *, mesh: str | None, encoding: str, timeout: int) -> EngineeringSummary:
    if not 1 <= timeout <= 600:
        raise ValueError("Worker timeout must be between 1 and 600 seconds")
    request, original = _request(input_path, mesh, encoding)
    root = output.absolute()
    prepare_bundle(root, request, timeout=timeout, input_encoding=encoding)
    try:
        with (root / f"original{input_path.suffix.lower()}").open("xb") as stream:
            stream.write(original)
        with (
            TemporaryDirectory(prefix="antenna-plot-") as plot_cache,
            (root / "diagnostics.log").open("xb") as log,
            subprocess.Popen(
                worker_command(root),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=worker_environment(plot_cache),
            ) as process,
        ):
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired, KeyboardInterrupt:
                process.kill()
                process.wait()
                raise
        if exit_code:
            raise RuntimeError("Solver/renderer failed; see diagnostics.log. No completed result was published")
        return complete_bundle(root)
    except (Exception, KeyboardInterrupt) as error:
        fail_bundle(root, error)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded thin-wire solver adapter (not a validated design workflow)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="Print the engineering request JSON Schema; no solver dependency required")
    solve = commands.add_parser("solve", help="Compute a new numerical result bundle")
    solve.add_argument("input", type=Path)
    solve.add_argument("--output", type=Path, required=True, help="New directory; its parent must already exist")
    solve.add_argument("--mesh", choices=("uniform80", "taper32"), help="Required for MAA only")
    solve.add_argument("--maa-encoding", default="cp936", choices=("cp936", "utf-8-sig", "cp1252", "ascii"))
    solve.add_argument("--timeout", type=int, default=120, help="Worker wall-clock deadline, 1..600 seconds")
    worker = commands.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "schema":
        print(json.dumps(EngineeringRequest.model_json_schema(), indent=2))
        return 0
    if args.command == "_worker":
        return _worker(args.output)
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be between 1 and 600 seconds")
    try:
        summary = run(args.input, args.output, mesh=args.mesh, encoding=args.maa_encoding, timeout=args.timeout)
    except KeyboardInterrupt:
        print(
            "Cancelled; owned solver process has exited. Check the bundle completion/failure receipt.", file=sys.stderr
        )
        return 130
    except ValidationError as error:
        details = error.errors(include_url=False, include_input=False, include_context=False)
        print(json.dumps({"error": "INVALID_INPUT", "details": details}), file=sys.stderr)
        return 2
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"antenna-design: {error}", file=sys.stderr)
        return 1
    print(summary.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
