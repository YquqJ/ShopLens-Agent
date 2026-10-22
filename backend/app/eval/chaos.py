"""MCP 保护层故障注入实验。

运行：
    uv run python -m app.eval.chaos

实验只使用本地故障工具，不访问真实商品源或订单服务。
"""
from __future__ import annotations

import asyncio
import json

from app.infra.fault_injection import Fault, FaultInjectedTool
from app.infra.protect import SourceError, SourceRegistry


def _run(coro):
    return asyncio.run(coro)


def _protected(faults, *, failure_threshold=3, cooldown_seconds=60, attempts=2):
    inner = FaultInjectedTool("haina_product_search_pro", faults, result={"ok": True})
    registry = SourceRegistry(
        failure_threshold=failure_threshold,
        cooldown_seconds=cooldown_seconds,
        retry_kwargs={"attempts": attempts, "base_seconds": 0, "max_seconds": 0},
    )
    tool = registry.protect({inner.name: inner})[inner.name]
    return inner, registry, tool


def _retry_case(fault: Fault) -> dict:
    inner, registry, tool = _protected([fault, None], attempts=2)
    result = _run(tool.ainvoke({}))
    metric = registry.tool_metrics_report()["haina_product"]
    return {
        "passed": result == {"ok": True} and inner.calls == 2 and metric["recovered_calls"] == 1,
        "attempts": metric["attempts"],
        "retries": metric["retries"],
        "recovered_calls": metric["recovered_calls"],
    }


def _non_transient_case() -> dict:
    inner, registry, tool = _protected([Fault.invalid_request()], attempts=3)
    try:
        _run(tool.ainvoke({}))
    except SourceError:
        pass
    metric = registry.tool_metrics_report()["haina_product"]
    return {
        "passed": inner.calls == 1 and metric["retries"] == 0,
        "attempts": metric["attempts"],
        "retries": metric["retries"],
    }


def _half_open_case() -> dict:
    inner, registry, tool = _protected(
        [Fault.network(), None], failure_threshold=1, cooldown_seconds=0, attempts=1
    )
    try:
        _run(tool.ainvoke({}))
    except SourceError:
        pass
    result = _run(tool.ainvoke({}))
    metric = registry.tool_metrics_report()["haina_product"]
    transitions = metric["breaker_transitions"]
    return {
        "passed": result == {"ok": True} and metric["breaker_state"] == "closed",
        "attempts": metric["attempts"],
        "breaker_transitions": transitions,
    }


def run_chaos_suite() -> dict:
    """执行固定故障场景并返回可记录到实验报告的结构化结果。"""
    cases = [
        {"name": "network_retry_recovery", **_retry_case(Fault.network())},
        {"name": "rate_limit_retry_recovery", **_retry_case(Fault.rate_limit())},
        {"name": "server_error_retry_recovery", **_retry_case(Fault.server_error(503))},
        {"name": "non_transient_fail_fast", **_non_transient_case()},
        {"name": "breaker_half_open_recovery", **_half_open_case()},
    ]
    return {
        "total": len(cases),
        "passed": sum(1 for case in cases if case["passed"]),
        "cases": cases,
    }


def main() -> int:
    report = run_chaos_suite()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
