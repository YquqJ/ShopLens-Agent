import asyncio

import pytest

from app.eval.chaos import run_chaos_suite
from app.infra.fault_injection import Fault, FaultInjectedTool
from app.infra.protect import SourceError, SourceRegistry


@pytest.mark.anyio
async def test_fault_injected_tool_emits_configured_failure_then_success() -> None:
    tool = FaultInjectedTool(
        "haina_product_search_pro",
        [Fault.network("connection reset"), None],
        result={"data": [{"title": "test-product", "page_price": 99}]},
    )

    with pytest.raises(ConnectionError, match="connection reset"):
        await tool.ainvoke({})

    assert await tool.ainvoke({}) == {"data": [{"title": "test-product", "page_price": 99}]}
    assert tool.calls == 2


@pytest.mark.anyio
async def test_fault_injection_proves_retry_recovery_for_rate_limit_and_server_error() -> None:
    for fault in (Fault.rate_limit(), Fault.server_error(503)):
        inner = FaultInjectedTool("haina_product_search_pro", [fault, None], result={"ok": True})
        registry = SourceRegistry(
            retry_kwargs={"attempts": 2, "base_seconds": 0, "max_seconds": 0}
        )
        tool = registry.protect({inner.name: inner})[inner.name]

        assert await tool.ainvoke({}) == {"ok": True}
        metrics = registry.tool_metrics_report()["haina_product"]
        assert inner.calls == 2
        assert metrics["recovered_calls"] == 1
        assert metrics["retry_recovery_rate"] == 1.0


@pytest.mark.anyio
async def test_fault_injection_does_not_retry_non_transient_errors() -> None:
    inner = FaultInjectedTool("haina_product_search_pro", [Fault.invalid_request()])
    registry = SourceRegistry(
        retry_kwargs={"attempts": 3, "base_seconds": 0, "max_seconds": 0}
    )
    tool = registry.protect({inner.name: inner})[inner.name]

    with pytest.raises(SourceError, match="ValueError"):
        await tool.ainvoke({})

    assert inner.calls == 1
    metrics = registry.tool_metrics_report()["haina_product"]
    assert metrics["retries"] == 0


def test_fault_injection_opens_breaker_and_skips_downstream_calls() -> None:
    inner = FaultInjectedTool(
        "haina_product_search_pro",
        [Fault.network(), Fault.network(), Fault.network(), None],
        result={"ok": True},
    )
    registry = SourceRegistry(
        failure_threshold=3,
        cooldown_seconds=60,
        retry_kwargs={"attempts": 1, "base_seconds": 0, "max_seconds": 0},
    )
    tool = registry.protect({inner.name: inner})[inner.name]

    for _ in range(3):
        with pytest.raises(SourceError):
            asyncio.run(tool.ainvoke({}))

    with pytest.raises(SourceError, match="熔断打开"):
        asyncio.run(tool.ainvoke({}))

    metrics = registry.tool_metrics_report()["haina_product"]
    assert inner.calls == 3
    assert metrics["circuit_open_skips"] == 1
    assert metrics["breaker_state"] == "open"


def test_fault_injection_half_open_probe_recovers_service(monkeypatch) -> None:
    from app.infra import breaker

    now = 10.0
    monkeypatch.setattr(breaker.time, "monotonic", lambda: now)
    inner = FaultInjectedTool(
        "haina_product_search_pro",
        [Fault.network(), Fault.network(), Fault.network(), None],
        result={"ok": True},
    )
    registry = SourceRegistry(
        failure_threshold=3,
        cooldown_seconds=5,
        retry_kwargs={"attempts": 1, "base_seconds": 0, "max_seconds": 0},
    )
    tool = registry.protect({inner.name: inner})[inner.name]

    for _ in range(3):
        with pytest.raises(SourceError):
            asyncio.run(tool.ainvoke({}))

    now += 5
    assert asyncio.run(tool.ainvoke({})) == {"ok": True}
    metrics = registry.tool_metrics_report()["haina_product"]
    assert inner.calls == 4
    assert metrics["breaker_state"] == "closed"
    assert metrics["breaker_transitions"]["open_to_half_open"] == 1
    assert metrics["breaker_transitions"]["half_open_to_closed"] == 1


def test_chaos_suite_reports_all_protection_behaviors() -> None:
    report = run_chaos_suite()

    assert report["passed"] == report["total"] == 5
    assert {case["name"] for case in report["cases"]} == {
        "network_retry_recovery",
        "rate_limit_retry_recovery",
        "server_error_retry_recovery",
        "non_transient_fail_fast",
        "breaker_half_open_recovery",
    }
