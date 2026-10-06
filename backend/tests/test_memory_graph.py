import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from app.agents.graph import build_graph
from app.agents.nodes import memory, route
from app.agents.nodes.memory import build_effective_preferences, parse_memory_command
from app.api.chat import ChatRequest, ResumeRequest


def test_parse_memory_command_saves_only_explicit_durable_catalog_preferences() -> None:
    command = parse_memory_command("请记住我以后优先淘宝，喜欢黑色和简约风，纯棉也可以")

    assert command == {
        "preferred_colors": ["黑色"],
        "preferred_styles": ["简约"],
        "preferred_materials": ["纯棉"],
    }


def test_parse_memory_command_updates_only_unambiguous_durable_values() -> None:
    command = parse_memory_command("请更新我的风格偏好，以后优先淘宝，更喜欢复古，预算300通勤穿")

    assert command == {
        "preferred_styles": ["复古"],
    }
    assert "budget" not in command
    assert "scene" not in command


def test_parse_memory_command_removes_only_named_values() -> None:
    assert parse_memory_command("忘记我的黑色和深蓝") == {
        "remove_values": {"preferred_colors": ["黑色", "深蓝"]},
    }


@pytest.mark.parametrize(("text", "expected"), [
    ("忘记我的颜色偏好中的黑色", {"remove_values": {"preferred_colors": ["黑色"]}}),
    ("忘记我的风格偏好中的简约", {"remove_values": {"preferred_styles": ["简约"]}}),
])
def test_parse_memory_command_removes_values_when_a_field_is_named(text: str, expected: dict) -> None:
    assert parse_memory_command(text) == expected


def test_parse_memory_command_forgets_named_durable_field() -> None:
    assert parse_memory_command("请忘记我的材质偏好") == {
        "forget": ["preferred_materials"],
    }


@pytest.mark.parametrize("text", [
    "请优先推荐黑色外套，预算500",
    "这次记住我喜欢黑色，预算500",
    "这次通勤穿，优先推荐纯棉衬衫",
    "我不喜欢黑色",
])
def test_parse_memory_command_ignores_temporary_or_ambiguous_phrases(text: str) -> None:
    assert parse_memory_command(text) == {}


def test_parse_memory_command_treats_commute_as_session_scene_not_durable_style() -> None:
    assert parse_memory_command("请更新我的风格偏好为通勤") == {}


def test_effective_preferences_ignore_retired_platform_memory() -> None:
    loaded = {
        "preferred_colors": ["黑色", "深蓝"],
        "preferred_styles": ["简约"],
        "preferred_platforms": ["淘宝"],  # legacy persisted row
    }
    session = {"category": "衬衫", "budget": 400, "style": "通勤", "material": "亚麻"}

    assert build_effective_preferences(session, loaded) == {
        "category": "衬衫",
        "budget": 400,
        "style": "通勤",
        "material": "亚麻",
        "preferred_colors": ["黑色", "深蓝"],
        "preferred_styles": ["简约"],
    }


def test_profile_key_uses_browser_profile_then_thread_fallback() -> None:
    assert memory.profile_key_from_config({"configurable": {
        "thread_id": "thread-1", "profile_id": "browser-1",
    }}) == "browser-1"
    assert memory.profile_key_from_config({"configurable": {"thread_id": "thread-1"}}) == "thread-1"


def test_chat_and_resume_requests_accept_optional_browser_profile_id() -> None:
    assert ChatRequest(message="hi", thread_id="thread-1", profile_id="browser-1").profile_id == "browser-1"
    assert ResumeRequest(thread_id="thread-1", confirm=True, profile_id="browser-1").profile_id == "browser-1"


@pytest.mark.anyio
async def test_memory_nodes_use_config_profile_id_and_emit_json_safe_events(monkeypatch) -> None:
    read = AsyncMock(return_value={"preferred_colors": ["黑色"]})
    write = AsyncMock(return_value={"preferred_colors": ["黑色"]})
    monkeypatch.setattr(memory.profile, "get_preferences", read)
    monkeypatch.setattr(memory.profile, "apply_updates", write)
    config = {"configurable": {"thread_id": "thread-1", "profile_id": "browser-1"}}

    loaded = await memory.load_memory_node({}, config)
    captured = await memory.capture_memory_node({
        "messages": [HumanMessage("记住我喜欢黑色")],
    }, config)

    read.assert_awaited_once_with("browser-1")
    write.assert_awaited_once_with({"preferred_colors": ["黑色"]}, "browser-1")
    assert json.loads(json.dumps(loaded)) == loaded
    assert json.loads(json.dumps(captured)) == captured


def test_graph_runs_load_before_clarify_and_capture() -> None:
    edges = {(edge.source, edge.target) for edge in build_graph().get_graph().edges}

    assert ("__start__", "load_memory") in edges
    assert ("load_memory", "route") in edges
    assert ("route", "clarify") in edges
    assert ("route", "capture_memory") in edges
    assert ("clarify", "capture_memory") in edges
    assert ("capture_memory", "retrieve") in edges


@pytest.mark.anyio
async def test_route_fast_paths_explicit_memory_command_without_llm(monkeypatch) -> None:
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("explicit memory command should not call the router LLM")

    monkeypatch.setattr(route, "get_structured_llm", fail_if_called)

    result = await route.route_node({
        "messages": [HumanMessage("请记住我喜欢黑色")],
        "preferences": {},
    })

    assert result["intent"] == "memory"
    assert result["reply"] == "已更新跨会话偏好。"


@pytest.mark.anyio
async def test_route_keeps_existing_shopping_context_for_follow_up(monkeypatch) -> None:
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("shopping follow-up should use the existing context fast path")

    monkeypatch.setattr(route, "get_structured_llm", fail_if_called)

    result = await route.route_node({
        "messages": [HumanMessage("预算 300")],
        "preferences": {"category": "衬衫"},
    })

    assert result["intent"] == "shopping"
