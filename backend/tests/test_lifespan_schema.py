from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import main


class _FakeSaver:
    @classmethod
    def from_conn_string(cls, _dsn: str) -> "_FakeSaver":
        return cls()

    async def __aenter__(self) -> "_FakeSaver":
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def setup(self) -> None:
        return None


class _FakeToolbox:
    tools = {}

    async def __aenter__(self) -> "_FakeToolbox":
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class _FakeRegistry:
    def protect(self, tools: dict) -> dict:
        return tools


@pytest.mark.anyio
async def test_lifespan_initializes_application_metadata_before_graph_setup(monkeypatch) -> None:
    events: list[str] = []

    async def init_metadata() -> None:
        events.append("init_db")

    async def build_toolbox() -> _FakeToolbox:
        return _FakeToolbox()

    def build_graph(*, checkpointer):
        assert events == ["init_db"]
        assert isinstance(checkpointer, _FakeSaver)
        return "graph"

    monkeypatch.setattr(main, "init_db", AsyncMock(side_effect=init_metadata))
    monkeypatch.setattr(main, "AsyncPostgresSaver", _FakeSaver)
    monkeypatch.setattr(main, "build_graph", build_graph)
    monkeypatch.setattr(main.adapter, "build_persistent_toolbox", build_toolbox)
    monkeypatch.setattr(main, "SourceRegistry", _FakeRegistry)
    monkeypatch.setattr(main, "setup_tracer", lambda: "tracer")
    app = SimpleNamespace(state=SimpleNamespace())

    async with main.lifespan(app):
        assert app.state.graph == "graph"

    main.init_db.assert_awaited_once_with()
