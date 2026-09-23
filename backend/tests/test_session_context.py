import inspect
from types import SimpleNamespace

import pytest

from app.agents import state
from app.api import chat


def test_next_chat_after_completed_order_resets_previous_shopping_preferences() -> None:
    prior_preferences = {"category": "卫衣", "budget": 3000, "color": "白色"}

    reset_signal = {"__reset_session_preferences__": True}

    assert state.merge_preferences(prior_preferences, reset_signal) == {}
    assert "reset_preferences" in inspect.signature(state.initial_state).parameters


def test_next_chat_without_completed_order_keeps_multi_turn_shopping_preferences() -> None:
    prior_preferences = {"category": "卫衣"}

    next_turn = state.initial_state("预算 500")

    assert state.merge_preferences(prior_preferences, next_turn["preferences"]) == prior_preferences


def test_only_placed_orders_start_a_fresh_shopping_context() -> None:
    completed = getattr(state, "is_completed_order", lambda _value: False)

    assert completed({"order": {"status": "placed"}}) is True
    assert completed({"order": {"status": "cancelled"}}) is False
    assert completed({"order": None}) is False


class _CompletedOrderGraph:
    async def aget_state(self, _cfg):
        return SimpleNamespace(values={"order": {"status": "placed"}})


@pytest.mark.anyio
async def test_chat_payload_starts_fresh_context_after_a_completed_order() -> None:
    builder = getattr(chat, "build_chat_payload", None)

    assert builder is not None
    payload = await builder(_CompletedOrderGraph(), {}, "平台我选择京东")
    assert state.merge_preferences({"category": "卫衣", "budget": 3000}, payload["preferences"]) == {}
