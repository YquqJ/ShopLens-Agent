from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage

from app.api.chat import (
    CancelProductRequest,
    SelectProductRequest,
    build_chat_payload,
    cancel_product,
    select_product,
)


class _Graph:
    def __init__(self):
        self.updated = None

    async def aget_state(self, _config):
        return SimpleNamespace(values={
            "recommendation": {
                "items": [{
                    "sku_id": "ZDM01",
                    "title": "真实外套",
                    "url": "https://mall.example/item",
                }],
            },
        })

    async def aupdate_state(self, config, update):
        self.updated = (config, update)
        return config


@pytest.mark.anyio
async def test_select_product_persists_agent_message_and_clears_recommendation(monkeypatch):
    graph = _Graph()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=graph)))
    monkeypatch.setattr("app.api.chat.require_conversation_owner", AsyncMock(return_value=1))

    result = await select_product(
        SelectProductRequest(
            thread_id="thread-1",
            profile_id="profile-1",
            sku_id="ZDM01",
            title="真实外套",
            url="https://mall.example/item",
        ),
        request,
    )

    assert result == {"selected": True}
    config, update = graph.updated
    assert config == {"configurable": {"thread_id": "thread-1"}}
    assert isinstance(update["messages"][0], AIMessage)
    assert update["messages"][0].content == "已选择「真实外套」\n链接：https://mall.example/item"
    assert update["recommendation"] == {}
    assert update["session_reset_pending"] is True


class _ResetGraph:
    async def aget_state(self, _config):
        return SimpleNamespace(values={
            "preferences": {"category": "外套", "budget": 300, "gender": "女款"},
            "session_reset_pending": True,
            "order": None,
        })


@pytest.mark.anyio
async def test_next_chat_after_real_selection_resets_short_term_preferences():
    payload = await build_chat_payload(
        _ResetGraph(),
        {"configurable": {"thread_id": "thread-1"}},
        "我想买一双鞋",
    )

    assert payload["preferences"] == {"__reset_session_preferences__": True}
    assert payload["hop_count"] == 0


@pytest.mark.anyio
async def test_select_product_rejects_item_not_in_current_recommendation(monkeypatch):
    graph = _Graph()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=graph)))
    monkeypatch.setattr("app.api.chat.require_conversation_owner", AsyncMock(return_value=1))

    with pytest.raises(HTTPException) as exc_info:
        await select_product(
            SelectProductRequest(
                thread_id="thread-1",
                profile_id="profile-1",
                sku_id="OTHER",
                title="未知商品",
                url="https://mall.example/other",
            ),
            request,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_cancel_product_persists_agent_message_without_resetting_context(monkeypatch):
    graph = _Graph()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=graph)))
    monkeypatch.setattr("app.api.chat.require_conversation_owner", AsyncMock(return_value=1))

    result = await cancel_product(
        CancelProductRequest(thread_id="thread-1", profile_id="profile-1"),
        request,
    )

    assert result == {"cancelled": True}
    _config, update = graph.updated
    assert update["messages"][0].content == "未选择推荐商品"
    assert update["recommendation"] == {}
    assert "session_reset_pending" not in update
