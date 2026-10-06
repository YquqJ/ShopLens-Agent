from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage

from app.api.chat import SelectProductRequest, select_product


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
