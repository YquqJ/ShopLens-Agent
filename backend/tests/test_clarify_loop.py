"""澄清不应无限重复追问：省略回答要继承需求，追问只问缺失字段。"""
from langchain_core.messages import AIMessage, HumanMessage

import pytest

from app.agents.nodes import clarify


class _Structured:
    def __init__(self, preferences: dict, question: str = "") -> None:
        self._preferences = preferences
        self._question = question
        self.messages = []

    async def ainvoke(self, messages):
        self.messages = messages
        return clarify.ClarifyResult(
            needs_clarification=bool(self._question),
            preferences=clarify.Preferences(**self._preferences),
            clarify_question=self._question,
        )


@pytest.mark.anyio
async def test_short_budget_answer_reuses_known_category(monkeypatch) -> None:
    structured = _Structured({}, question="为了更精准地推荐，预算大概多少？")
    monkeypatch.setattr(clarify, "get_structured_llm", lambda *_args: structured)

    result = await clarify.clarify_node({
        "messages": [
            HumanMessage("我要买一件透气显瘦的T恤"),
            AIMessage("请问您的预算是多少？"),
            HumanMessage("预算100"),
        ],
        "preferences": {"category": "T恤", "requirements": "透气显瘦"},
        "clarify_question": "请问您的预算是多少？",
        "hop_count": 1,
    })

    assert result["needs_action"] == "shopping"
    assert result["clarify_question"] == ""
    assert result["trace_preferences"]["budget"] == 100


@pytest.mark.anyio
async def test_clarify_prompt_includes_known_preferences_and_last_question(monkeypatch) -> None:
    structured = _Structured({})
    monkeypatch.setattr(clarify, "get_structured_llm", lambda *_args: structured)

    await clarify.clarify_node({
        "messages": [
            HumanMessage("我要买一件T恤"),
            AIMessage("请问您的预算是多少？"),
            HumanMessage("预算100"),
        ],
        "preferences": {"category": "T恤"},
        "clarify_question": "请问您的预算是多少？",
        "hop_count": 1,
    })

    prompt = "\n".join(str(message.content) for message in structured.messages)
    assert "T恤" in prompt
    assert "请问您的预算是多少" in prompt


@pytest.mark.anyio
async def test_repeated_clarification_asks_only_missing_field(monkeypatch) -> None:
    structured = _Structured({"category": "T恤"}, question="请问您的预算是多少？需要男款还是女款？")
    monkeypatch.setattr(clarify, "get_structured_llm", lambda *_args: structured)

    result = await clarify.clarify_node({
        "messages": [HumanMessage("男款")],
        "preferences": {"category": "T恤", "gender": "男款", "budget": 100},
        "clarify_question": "请问您的预算是多少？需要男款还是女款？",
        "hop_count": 2,
    })

    assert result["needs_action"] == "shopping"
    assert result["clarify_question"] == ""
