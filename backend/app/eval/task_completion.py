"""端到端任务完成率评测。

评测口径：每条任务预先声明期望的业务结果，由完整 StateGraph 执行并按最终状态判定，
不使用 LLM-as-judge。脚本同时保留节点轨迹和失败原因，便于定位是澄清、检索、知识增强还是下单失败。
重复运行：TASK_EVAL_REPEATS=3 uv run python -m app.eval.task_completion
包含真实下单写库；需先配置隔离评测库，再显式设置 TASK_EVAL_ALLOW_DB_WRITES=1。
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import time
from pathlib import Path
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from sqlalchemy import delete, select

from app.db.models import Conversation, Order, User
from app.db.session import async_session_factory, init_db

TASK_SET = Path(__file__).with_name("task_set.json")


def task_succeeded(task: dict, observation: dict) -> bool:
    """按任务类型判定是否完成，所有判定均基于结构化状态而非自然语言相似度。"""
    task_type = task.get("type")
    nodes = set(observation.get("nodes") or [])
    if task_type == "recommendation":
        return observation.get("recommendation_items", 0) > 0 and "recommend" in nodes
    if task_type == "clarification":
        return (
            observation.get("clarify_count", 0) >= 1
            and observation.get("recommendation_items", 0) > 0
            and "recommend" in nodes
        )
    if task_type == "order":
        return observation.get("order_status") == "placed" and "place_order" in nodes
    if task_type == "order_search":
        if "query_orders" not in nodes or observation.get("order_error"):
            return False
        expected_ids = task.get("expected_order_ids")
        actual = observation.get("order_results") or []
        if expected_ids is not None:
            return [item.get("order_id") for item in actual] == expected_ids
        return len(actual) == task.get("expected_count", len(actual))
    if task_type == "order_summary":
        if "summarize_orders" not in nodes or observation.get("order_error"):
            return False
        actual = observation.get("order_summary") or {}
        expected = task.get("expected_summary") or {}
        return all(actual.get(key) == value for key, value in expected.items())
    if task_type == "order_clarification":
        if observation.get("order_clarification_count", 0) < 1:
            return False
        expected_terminal = task.get("expected_terminal")
        if expected_terminal == "order_search":
            if "query_orders" not in nodes or observation.get("order_error"):
                return False
            return len(observation.get("order_results") or []) == task.get("expected_count", 0)
        if expected_terminal == "order_summary":
            if "summarize_orders" not in nodes or observation.get("order_error"):
                return False
            actual = observation.get("order_summary") or {}
            expected = task.get("expected_summary") or {}
            return all(actual.get(key) == value for key, value in expected.items())
        return False
    if task_type == "cancel":
        return (
            observation.get("order_status") == "cancelled" and "confirm" in nodes
        ) or observation.get("intent") == "cancel"
    if task_type == "other":
        return observation.get("intent") == "other" and bool(observation.get("reply"))
    raise ValueError(f"未知任务类型: {task_type!r}")


def completion_summary(results: list[dict]) -> dict:
    """汇总任务完成率，并保留失败任务，供脚本和测试复用。"""
    total = len(results)
    passed = sum(1 for result in results if result.get("passed"))
    successful = [result for result in results if result.get("passed")]
    by_type = {}
    for task_type in sorted({result.get("type", "unknown") for result in results}):
        typed = [result for result in results if result.get("type", "unknown") == task_type]
        typed_passed = sum(1 for result in typed if result.get("passed"))
        by_type[task_type] = {
            "passed": typed_passed,
            "total": len(typed),
            "completion_rate": typed_passed / len(typed) if typed else 0.0,
        }

    durations = [result["duration_ms"] for result in successful if result.get("duration_ms") is not None]
    tokens = [result["total_tokens"] for result in successful if result.get("total_tokens") is not None]
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "completion_rate": passed / total if total else 0.0,
        "by_type": by_type,
        "successful_task_metrics": {
            "latency_ms": _sample_summary(durations),
            "tokens": _sample_summary(tokens),
        },
        "failures": [
            {"id": result.get("id"), "reason": result.get("reason", "未满足完成条件")}
            for result in results
            if not result.get("passed")
        ],
    }


def _sample_summary(samples: list[float | int]) -> dict:
    values = sorted(samples)
    if not values:
        return {"count": 0, "mean": None, "median": None, "p95": None}
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 2),
        "median": round(statistics.median(values), 2),
        "p95": round(values[math.ceil(0.95 * len(values)) - 1], 2),
    }


def require_order_eval_opt_in(tasks: list[dict]) -> None:
    """Require an explicit opt-in before the evaluator writes isolated fixtures."""
    writes_database = any(
        task.get("type") in {"order", "order_search", "order_summary", "order_clarification"}
        for task in tasks
    )
    if writes_database and os.getenv("TASK_EVAL_ALLOW_DB_WRITES") != "1":
        raise RuntimeError(
            "任务集包含下单或订单查询场景，会写入隔离评测数据库；请先配置隔离评测库并设置 "
            "TASK_EVAL_ALLOW_DB_WRITES=1 后再运行"
        )


class UsageCollector(BaseCallbackHandler):
    """汇总模型回调提供的实际 Token 用量；不以 tokenizer 估算值冒充实测。"""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.calls_with_usage = 0

    def on_llm_end(self, response, **kwargs) -> None:
        usage = None
        for batch in getattr(response, "generations", []) or []:
            for generation in batch:
                message_usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
                if message_usage:
                    usage = message_usage
                    break
            if usage:
                break
        if usage is None:
            llm_output = getattr(response, "llm_output", None) or {}
            usage = llm_output.get("token_usage") or llm_output.get("usage") or {}

        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
        total_tokens = usage.get("total_tokens") or input_tokens + output_tokens
        if total_tokens:
            self.input_tokens += int(input_tokens)
            self.output_tokens += int(output_tokens)
            self.total_tokens += int(total_tokens)
            self.calls_with_usage += 1


def _merge_observation(observation: dict, update: dict, node: str) -> None:
    observation.setdefault("nodes", []).append(node)
    if "intent" in update:
        observation["intent"] = update["intent"]
    if "reply" in update:
        observation["reply"] = update["reply"]
    if "clarify_question" in update and update["clarify_question"]:
        observation["clarify_count"] = observation.get("clarify_count", 0) + 1
    if "order_clarification" in update and update["order_clarification"]:
        observation["order_clarification_count"] = (
            observation.get("order_clarification_count", 0) + 1
        )
    if "order_results" in update:
        observation["order_results"] = update.get("order_results") or []
    if "order_summary" in update and update.get("order_summary") is not None:
        observation["order_summary"] = update["order_summary"]
    if "order_error" in update:
        observation["order_error"] = update["order_error"]
    recommendation = update.get("recommendation")
    if isinstance(recommendation, dict):
        observation["recommendation_items"] = len(recommendation.get("items") or [])
    order = update.get("order")
    if isinstance(order, dict):
        observation["order_status"] = order.get("status")


async def _run_turn(graph, config: dict, message: str, observation: dict) -> bool:
    from app.agents.state import initial_state

    async for event in graph.astream(initial_state(message), config, stream_mode="updates"):
        for node, update in event.items():
            if node == "__interrupt__" or not isinstance(update, dict):
                continue
            _merge_observation(observation, update, node)
    snapshot = await graph.aget_state(config)
    return bool(snapshot.interrupts)


_ORDER_FIXTURES = {
    "yesterday_mixed": [
        {"title": "降噪耳机", "category": "耳机", "platform": "jd", "price": 299.0, "day_offset": -1},
        {"title": "手机壳", "category": "手机配件", "platform": "taobao", "price": 39.0, "day_offset": -1},
        {"title": "跑鞋", "category": "鞋", "platform": "jd", "price": 399.0, "day_offset": -3},
    ],
    "recent_headphones": [
        {"title": "降噪耳机", "category": "耳机", "platform": "jd", "price": 299.0, "day_offset": -2},
        {"title": "蓝牙耳机", "category": "耳机", "platform": "jd", "price": 100.0, "day_offset": -10},
        {"title": "衬衫", "category": "衬衫", "platform": "taobao", "price": 200.0, "day_offset": -40},
    ],
    "platform_split": [
        {"title": "键盘", "category": "键盘", "platform": "jd", "price": 199.0, "day_offset": -1},
        {"title": "鼠标", "category": "鼠标", "platform": "jd", "price": 100.0, "day_offset": -2},
        {"title": "手机壳", "category": "手机配件", "platform": "taobao", "price": 50.0, "day_offset": -3},
    ],
    "category_split": [
        {"title": "降噪耳机", "category": "耳机", "platform": "jd", "price": 299.0, "day_offset": -2},
        {"title": "衬衫", "category": "衬衫", "platform": "taobao", "price": 200.0, "day_offset": -3},
        {"title": "外套", "category": "外套", "platform": "jd", "price": 500.0, "day_offset": -4},
    ],
    "empty": [
        {"title": "降噪耳机", "category": "耳机", "platform": "jd", "price": 299.0, "day_offset": -20},
    ],
}


async def _seed_order_fixture(task: dict, profile_key: str) -> None:
    fixture_name = task.get("fixture")
    specs = _ORDER_FIXTURES.get(fixture_name)
    if specs is None:
        raise ValueError(f"未知订单评测夹具: {fixture_name!r}")
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    async with async_session_factory() as session:
        user = User(external_id=profile_key)
        session.add(user)
        await session.flush()
        conversation = Conversation(thread_id=f"fixture-{profile_key}", user_id=user.id)
        session.add(conversation)
        await session.flush()
        for index, spec in enumerate(specs, start=1):
            created_at = (now + timedelta(days=spec["day_offset"])).replace(
                hour=12, minute=index, second=0, microsecond=0
            )
            session.add(Order(
                order_intent_id=f"{profile_key}-intent-{index}",
                order_number=f"{profile_key[-8:]}{index:04d}",
                conversation_id=conversation.id,
                user_id=user.id,
                sku_id=f"EVAL-{profile_key[-8:]}-{index}",
                title_snapshot=spec["title"],
                category_snapshot=spec["category"],
                platform=spec["platform"],
                catalog_price=spec["price"],
                quoted_price=spec["price"],
                shipping_fee=0,
                final_price=spec["price"],
                status="placed",
                created_at=created_at,
                updated_at=created_at,
            ))
        await session.commit()


async def _cleanup_order_fixture(profile_key: str) -> None:
    async with async_session_factory() as session:
        user = await session.scalar(select(User).where(User.external_id == profile_key))
        if user is None:
            return
        await session.execute(delete(Order).where(Order.user_id == user.id))
        await session.execute(delete(Conversation).where(Conversation.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


async def _run_task(graph, tools: dict, task: dict) -> dict:
    from langgraph.types import Command

    started_at = time.perf_counter()
    usage = UsageCollector()
    thread_id = f"task-e2e-{uuid4().hex[:10]}"
    profile_key = f"task-profile-{uuid4().hex[:12]}"
    config = {
        "configurable": {
            "thread_id": thread_id,
            "profile_id": profile_key,
            "mcp_tools": tools,
        },
        "callbacks": [usage],
    }
    observation: dict = {}
    fixture_profile = profile_key if task.get("fixture") else None
    try:
        if fixture_profile:
            await _seed_order_fixture(task, fixture_profile)
        messages = task.get("messages") or [task["message"]]
        interrupted = False
        for message in messages:
            interrupted = await _run_turn(graph, config, message, observation)

        if task.get("resume") is not None:
            if not interrupted:
                raise RuntimeError("任务预期进入确认中断，但未发现 interrupt")
            resume = {"confirm": bool(task["resume"])}
            async for event in graph.astream(Command(resume=resume), config, stream_mode="updates"):
                for node, update in event.items():
                    if node == "__interrupt__" or not isinstance(update, dict):
                        continue
                    _merge_observation(observation, update, node)

        snapshot = await graph.aget_state(config)
        values = snapshot.values or {}
        observation.setdefault("intent", values.get("intent"))
        observation.setdefault("reply", values.get("reply"))
        observation.setdefault("order_status", (values.get("order") or {}).get("status"))
        observation.setdefault("order_results", values.get("order_results") or [])
        observation.setdefault("order_summary", values.get("order_summary"))
        observation.setdefault("order_error", values.get("order_error"))
        recommendation = values.get("recommendation") or {}
        observation.setdefault("recommendation_items", len(recommendation.get("items") or []))
        passed = task_succeeded(task, observation)
        return {
            "id": task["id"],
            "type": task["type"],
            "passed": passed,
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "input_tokens": usage.input_tokens if usage.calls_with_usage else None,
            "output_tokens": usage.output_tokens if usage.calls_with_usage else None,
            "total_tokens": usage.total_tokens if usage.calls_with_usage else None,
            "llm_calls_with_usage": usage.calls_with_usage,
            "observation": observation,
            "reason": "" if passed else "未满足任务预期状态",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "id": task["id"],
            "type": task["type"],
            "passed": False,
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "input_tokens": usage.input_tokens if usage.calls_with_usage else None,
            "output_tokens": usage.output_tokens if usage.calls_with_usage else None,
            "total_tokens": usage.total_tokens if usage.calls_with_usage else None,
            "llm_calls_with_usage": usage.calls_with_usage,
            "observation": observation,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if fixture_profile:
            await _cleanup_order_fixture(fixture_profile)


async def run_tasks(repeats: int = 1) -> tuple[list[dict], dict]:
    from langgraph.checkpoint.memory import MemorySaver

    from app.agents.graph import build_graph
    from app.eval._harness import mcp_tools_ctx

    tasks = json.loads(TASK_SET.read_text(encoding="utf-8"))["tasks"]
    require_order_eval_opt_in(tasks)
    await init_db()
    graph = build_graph(checkpointer=MemorySaver())
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    results = []
    async with mcp_tools_ctx() as (tools, _registry):
        for repeat in range(1, repeats + 1):
            for task in tasks:
                result = await _run_task(graph, tools, task)
                result["repeat"] = repeat
                results.append(result)
    return results, completion_summary(results)


async def main() -> int:
    repeats = int(os.getenv("TASK_EVAL_REPEATS", "1"))
    try:
        results, summary = await run_tasks(repeats=repeats)
    except RuntimeError as exc:
        print(f"评测未启动：{exc}")
        return 2
    for result in results:
        mark = "PASS" if result["passed"] else "FAIL"
        token_text = f"{result['total_tokens']} tok" if result["total_tokens"] is not None else "Token 未回报"
        print(
            f"[{mark}] {result['id']} r{result['repeat']} · {result['type']} · "
            f"{result['duration_ms']:.0f}ms · {token_text} · {result['reason']}"
        )
    print(
        f"\n端到端任务完成率: {summary['passed']}/{summary['total']} "
        f"({summary['completion_rate']:.1%})"
    )
    print(f"按类型: {json.dumps(summary['by_type'], ensure_ascii=False)}")
    metrics = summary["successful_task_metrics"]
    print(f"成功任务耗时 ms (样本/均值/中位数/P95): {metrics['latency_ms']}")
    print(f"成功任务 Token (样本/均值/中位数/P95): {metrics['tokens']}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    from app.eval._harness import apply_loop_policy

    apply_loop_policy()
    raise SystemExit(asyncio.run(main()))
