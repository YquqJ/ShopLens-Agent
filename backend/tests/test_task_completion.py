import json
from collections import Counter
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
import pytest

from app.eval.task_completion import (
    TASK_SET,
    UsageCollector,
    completion_summary,
    require_order_eval_opt_in,
    task_succeeded,
)


def test_task_set_has_one_hundred_balanced_agent_workflow_cases() -> None:
    tasks = json.loads(TASK_SET.read_text(encoding="utf-8"))["tasks"]

    assert len(tasks) == 100
    assert len({task["id"] for task in tasks}) == 100
    assert Counter(task["type"] for task in tasks) == {
        "recommendation": 20,
        "clarification": 20,
        "order": 12,
        "cancel": 8,
        "order_search": 12,
        "order_summary": 12,
        "order_clarification": 6,
        "other": 10,
    }
    assert all(task.get("message") or task.get("messages") for task in tasks)
    assert all(len(task["messages"]) >= 2 for task in tasks if task["type"] == "clarification")
    assert all(task.get("fixture") for task in tasks if task["type"] in {
        "order_search", "order_summary", "order_clarification",
    })
    assert all(task.get("resume") is True for task in tasks if task["type"] == "order")


def test_recommendation_task_requires_a_non_empty_recommendation() -> None:
    assert task_succeeded(
        {"type": "recommendation"},
        {"recommendation_items": 1, "nodes": ["retrieve", "recommend"]},
    ) is True
    assert task_succeeded(
        {"type": "recommendation"},
        {"recommendation_items": 0, "nodes": ["retrieve", "recommend"]},
    ) is False


def test_order_task_requires_successful_order_placement() -> None:
    assert task_succeeded(
        {"type": "order"},
        {"order_status": "placed", "nodes": ["confirm", "place_order"]},
    ) is True
    assert task_succeeded(
        {"type": "order"},
        {"order_status": "confirmed", "nodes": ["confirm"]},
    ) is False


def test_cancel_and_other_tasks_use_their_expected_terminal_state() -> None:
    assert task_succeeded(
        {"type": "cancel"},
        {"intent": "cancel", "order_status": "cancelled", "nodes": ["route"]},
    ) is True
    assert task_succeeded(
        {"type": "other"},
        {"intent": "other", "reply": "我是导购助手", "nodes": ["route"]},
    ) is True


def test_clarification_task_requires_clarify_then_recommend() -> None:
    assert task_succeeded(
        {"type": "clarification"},
        {
            "clarify_count": 1,
            "recommendation_items": 1,
            "nodes": ["clarify", "route", "retrieve", "recommend"],
        },
    ) is True
    assert task_succeeded(
        {"type": "clarification"},
        {"clarify_count": 1, "recommendation_items": 0, "nodes": ["clarify"]},
    ) is False


def test_order_search_task_requires_query_tool_and_expected_result_count() -> None:
    assert task_succeeded(
        {"type": "order_search", "expected_count": 2},
        {
            "nodes": ["route", "parse_order_query", "query_orders"],
            "order_results": [{"order_id": 1}, {"order_id": 2}],
        },
    ) is True
    assert task_succeeded(
        {"type": "order_search", "expected_count": 2},
        {
            "nodes": ["route", "parse_order_query", "query_orders"],
            "order_results": [{"order_id": 1}],
        },
    ) is False


def test_order_summary_task_requires_aggregation_and_expected_metrics() -> None:
    assert task_succeeded(
        {
            "type": "order_summary",
            "expected_summary": {
                "order_count": 2,
                "total_amount": 399.0,
                "average_order_amount": 199.5,
            },
        },
        {
            "nodes": ["route", "parse_order_query", "summarize_orders"],
            "order_summary": {
                "order_count": 2,
                "total_amount": 399.0,
                "average_order_amount": 199.5,
                "groups": [],
            },
        },
    ) is True
    assert task_succeeded(
        {
            "type": "order_summary",
            "expected_summary": {"order_count": 2, "total_amount": 399.0},
        },
        {
            "nodes": ["route", "parse_order_query", "summarize_orders"],
            "order_summary": {"order_count": 2, "total_amount": 398.0},
        },
    ) is False


def test_order_clarification_task_requires_clarification_then_terminal_query() -> None:
    assert task_succeeded(
        {
            "type": "order_clarification",
            "expected_terminal": "order_search",
            "expected_count": 1,
        },
        {
            "order_clarification_count": 1,
            "nodes": [
                "route", "parse_order_query", "route",
                "parse_order_query", "query_orders",
            ],
            "order_results": [{"order_id": 1}],
        },
    ) is True
    assert task_succeeded(
        {
            "type": "order_clarification",
            "expected_terminal": "order_search",
            "expected_count": 1,
        },
        {
            "order_clarification_count": 0,
            "nodes": ["route", "parse_order_query", "query_orders"],
            "order_results": [{"order_id": 1}],
        },
    ) is False


def test_completion_summary_reports_latency_and_tokens_for_successful_tasks_only() -> None:
    summary = completion_summary([
        {"id": "a", "passed": True, "duration_ms": 100, "total_tokens": 120},
        {"id": "b", "passed": True, "duration_ms": 300, "total_tokens": 280},
        {"id": "c", "passed": False, "duration_ms": 900, "total_tokens": 1000},
    ])

    metrics = summary["successful_task_metrics"]
    assert summary["passed"] == 2
    assert metrics["latency_ms"] == {"count": 2, "mean": 200.0, "median": 200.0, "p95": 300.0}
    assert metrics["tokens"] == {"count": 2, "mean": 200.0, "median": 200.0, "p95": 280.0}


def test_usage_collector_uses_provider_reported_token_usage() -> None:
    collector = UsageCollector()
    response = LLMResult(
        generations=[[
            ChatGeneration(message=AIMessage(
                content="ok",
                usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            )),
        ]],
        llm_output={"token_usage": {"prompt_tokens": 99, "completion_tokens": 99, "total_tokens": 198}},
    )

    collector.on_llm_end(response)

    assert collector.input_tokens == 11
    assert collector.output_tokens == 7
    assert collector.total_tokens == 18


def test_order_evaluation_requires_explicit_database_write_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("TASK_EVAL_ALLOW_DB_WRITES", raising=False)
    with pytest.raises(RuntimeError, match="TASK_EVAL_ALLOW_DB_WRITES=1"):
        require_order_eval_opt_in([{"type": "order"}])

    monkeypatch.setenv("TASK_EVAL_ALLOW_DB_WRITES", "1")
    require_order_eval_opt_in([{"type": "order"}])
