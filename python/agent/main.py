"""CLI for the single-process local HTTP/WebSocket backend."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import threading
from contextlib import suppress
from pathlib import Path

import click
import uvicorn

from agent.api.app import create_app, loopback
from agent.config_manager import ConfigManager
from agent.core import AgentCore
from agent.event_bus import EventBus
from agent.observability import ConsoleObserver, Observability

logger = logging.getLogger("maigokite-agent")


def configure_logging() -> None:
    """Make backend diagnostics visible when launched by scripts/local.cjs."""
    level_name = os.getenv("MAIGOKITE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@click.group()
def cli() -> None:
    """Run the MaigoKite backend."""


def main_cli() -> None:
    configure_logging()
    cli()


@cli.command()
@click.option("--prompt", "-p", default=None)
@click.option("--config", "-c", default=None, type=click.Path(exists=True, dir_okay=False))
@click.option("--host", "-H", default="127.0.0.1")
@click.option("--port", "-P", default=8080, type=click.IntRange(1, 65535))
@click.option("--web-dir", default=None, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--interactive", "-i", is_flag=True)
def run(prompt: str | None, config: str | None, host: str, port: int, web_dir: Path | None, interactive: bool) -> None:
    """Serve the local API, optionally including a built Web UI."""
    if not loopback(host):
        raise click.BadParameter(
            "Only a loopback address is supported; remote authentication is not implemented.", param_hint="--host",
        )
    asyncio.run(start_agent(prompt, config, host, port, interactive, web_dir))


async def start_agent(prompt: str | None, config_path: str | None, host: str, port: int,
                      interactive: bool, web_dir: Path | None = None) -> None:
    # Consume before Core creates tools or child processes; never inherit desktop authentication into them.
    desktop_token = os.environ.pop("MAIGOKITE_DESKTOP_TOKEN", "")
    config = ConfigManager(config_path or os.getenv("MAIGOKITE_CONFIG"))
    await config.load()
    observability = Observability()
    observability.add_observer(ConsoleObserver())
    core = AgentCore(config, EventBus(), observability)
    if interactive and prompt:
        try:
            await core.initialize()
            logger.info("Agent response: %s", (await core.run(prompt))["response"])
        finally:
            await core.shutdown()
        return
    server = uvicorn.Server(uvicorn.Config(create_app(core, web_dir, desktop_token=desktop_token), host=host, port=port,
                            workers=1, proxy_headers=False, access_log=False,
                            timeout_graceful_shutdown=8, ws_max_size=65536, ws_max_queue=16))
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        server.should_exit = True

    def watch_owner() -> None:
        # The launcher owns stdin's write end; EOF requests the same lifespan cleanup as Ctrl+C.
        try:
            while sys.stdin.buffer.read(1):
                pass
        except (OSError, ValueError):
            pass
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(request_stop)

    # Reserve exclusively before initialize/recovery can modify the existing task database.
    listener = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(128)
        listener.setblocking(False)
        if os.getenv("MAIGOKITE_PARENT_PIPE") == "1":
            threading.Thread(target=watch_owner, name="backend-owner-pipe", daemon=True).start()
        await server.serve(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main_cli()
