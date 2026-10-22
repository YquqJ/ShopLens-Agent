from pathlib import Path

import pytest

from app.mcp import adapter, order_server


def test_order_mcp_server_installs_selector_policy_before_run(monkeypatch):
    assert hasattr(order_server, "main")
    events = []
    monkeypatch.setattr(order_server, "install_selector_event_loop_policy", lambda: events.append("selector"))
    monkeypatch.setattr(order_server.mcp, "run", lambda **kwargs: events.append(("run", kwargs)))

    order_server.main()

    assert events == ["selector", ("run", {"transport": "stdio"})]


def test_local_order_fastmcp_server_is_registered_as_stdio_connection():
    connections = adapter.server_connections()
    order = connections["orders"]

    assert order["transport"] == "stdio"
    assert order["command"]
    assert order["args"] == ["-m", "app.mcp.order_server"]
    assert Path(order["cwd"]) == adapter.BACKEND_DIR
    assert order["env"]["PYTHONPATH"].split(";")[0] == str(adapter.BACKEND_DIR)


@pytest.mark.anyio
async def test_order_mcp_tools_forward_profile_scope_to_order_service(monkeypatch):
    captured = {}

    async def fake_search(start_at, end_at, **kwargs):
        captured.update(start_at=start_at, end_at=end_at, **kwargs)
        return [{"order_id": 9, "platform": "jd", "final_price": 125}]

    monkeypatch.setattr(order_server.service, "search_orders", fake_search)
    summary = await order_server.summarize_orders(
        "2026-10-01T00:00:00+08:00",
        "2026-10-02T00:00:00+08:00",
        platform="jd",
        group_by="platform",
        profile_key="profile-9",
    )

    assert captured["profile_key"] == "profile-9"
    assert captured["platform"] == "jd"
    assert summary["order_count"] == 1
    assert summary["total_amount"] == 125.0
