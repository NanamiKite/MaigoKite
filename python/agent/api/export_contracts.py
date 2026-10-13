"""Export the real route schema without starting Core, opening storage or loading secrets."""
from __future__ import annotations

import json
import sys

from agent.api.app import create_app
from agent.config_manager import ConfigManager
from agent.core import AgentCore
from agent.event_bus import EventBus
from agent.observability import Observability
from agent.repository import SQLiteRepository


def main() -> None:
    config = ConfigManager()
    config.set("browser.enabled", False)
    core = AgentCore(config, EventBus(), Observability(), repository=SQLiteRepository(":memory:"))
    # Constructing routes does not enter the FastAPI lifespan.
    json.dump(create_app(core).openapi(), sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
