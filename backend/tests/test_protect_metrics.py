import asyncio

import pytest

from app.infra.protect import SourceRegistry


class FlakyTool:
    name = "price_jd_get_quote"

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    async def ainvoke(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("temporary source error")
        return {"price": 10}


def test_registry_reports_retry_recovery_and_attempt_counts() -> None:
    inner = FlakyTool(failures=1)
    registry = SourceRegistry(
        retry_kwargs={"attempts": 2, "base_seconds": 0, "max_seconds": 0},
    )
    tool = registry.protect({inner.name: inner})[inner.name]

    result = asyncio.run(tool.ainvoke({"sku_id": "SKU1"}))

    metric = registry.tool_metrics_report()["jd"]
    assert result == {"price": 10}
    assert metric["logical_calls"] == 1
    assert metric["attempts"] == 2
    assert metric["retries"] == 1
    assert metric["recovered_calls"] == 1
    assert metric["retry_recovery_rate"] == 1.0


def test_registry_reports_open_circuit_skips_without_calling_source() -> None:
    inner = FlakyTool(failures=10)
    registry = SourceRegistry(
        failure_threshold=1,
        cooldown_seconds=60,
        retry_kwargs={"attempts": 1, "base_seconds": 0, "max_seconds": 0},
    )
    tool = registry.protect({inner.name: inner})[inner.name]

    with pytest.raises(Exception):
        asyncio.run(tool.ainvoke({"sku_id": "SKU1"}))
    with pytest.raises(Exception):
        asyncio.run(tool.ainvoke({"sku_id": "SKU1"}))

    metric = registry.tool_metrics_report()["jd"]
    assert inner.calls == 1
    assert metric["logical_calls"] == 2
    assert metric["circuit_open_skips"] == 1


def test_registry_reports_retry_exhaustion_separately_from_recovery() -> None:
    inner = FlakyTool(failures=10)
    registry = SourceRegistry(
        failure_threshold=5,
        retry_kwargs={"attempts": 2, "base_seconds": 0, "max_seconds": 0},
    )
    tool = registry.protect({inner.name: inner})[inner.name]

    with pytest.raises(Exception):
        asyncio.run(tool.ainvoke({"sku_id": "SKU1"}))

    metric = registry.tool_metrics_report()["jd"]
    assert metric["attempts"] == 2
    assert metric["retries"] == 1
    assert metric["retry_failed_calls"] == 1
    assert metric["recovered_calls"] == 0
    assert metric["retry_recovery_rate"] == 0.0


def test_breaker_reports_three_state_transitions(monkeypatch) -> None:
    from app.infra.breaker import CircuitBreaker

    now = 10.0
    monkeypatch.setattr("app.infra.breaker.time.monotonic", lambda: now)
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=5)

    breaker.record_failure()
    assert breaker.state.value == "open"
    assert breaker.allow() is False

    now += 5
    assert breaker.state.value == "half_open"
    assert breaker.allow() is True
    breaker.record_success()

    assert breaker.state.value == "closed"
    assert breaker.transition_counts == {
        "closed_to_open": 1,
        "open_to_half_open": 1,
        "half_open_to_closed": 1,
        "half_open_to_open": 0,
    }


def test_half_open_probe_failure_reopens_breaker(monkeypatch) -> None:
    from app.infra.breaker import CircuitBreaker

    now = 10.0
    monkeypatch.setattr("app.infra.breaker.time.monotonic", lambda: now)
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=5)
    breaker.record_failure()

    now += 5
    assert breaker.allow() is True
    breaker.record_failure()

    assert breaker.state.value == "open"
    assert breaker.transition_counts["half_open_to_open"] == 1
