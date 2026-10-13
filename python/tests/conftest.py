from __future__ import annotations

import pytest
from agent.config_manager import ConfigManager
from agent.core import AgentCore
from agent.event_bus import EventBus
from agent.observability import Observability
from agent.repository import SQLiteRepository

from tests.fixtures.chat_model import FakeModel


@pytest.fixture
async def core(tmp_path):
    config = ConfigManager()
    config.set("workspace.root", str(tmp_path))
    config.set("permissions.default_mode", "read_only")
    instance = AgentCore(config, EventBus(), Observability(), FakeModel(), SQLiteRepository(":memory:"))
    await instance.initialize()
    yield instance
    await instance.shutdown()
