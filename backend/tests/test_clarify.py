import pytest
from langchain_core.messages import HumanMessage

from app.agents.nodes import clarify
from app.agents.state import merge_preferences


def test_normalize_preferences_preserves_explicit_platform():
    assert clarify.normalize_preferences({"platform": "jd"})["platform"] == "jd"


def test_normalize_preferences_maps_known_platform_aliases():
    assert clarify.normalize_preferences({"platform": "京东"})["platform"] == "jd"


def test_clothing_gender_is_not_required_for_other_product_categories():
    assert clarify._missing({"category": "手机", "budget": 3000}) == []


@pytest.mark.anyio
async def test_switching_category_clears_old_product_constraints(monkeypatch):
    class _Structured:
        async def ainvoke(self, _messages):
            return clarify.ClarifyResult(
                needs_clarification=False,
                preferences=clarify.Preferences(category="手机"),
                clarify_question="",
            )

    monkeypatch.setattr(clarify, "get_structured_llm", lambda *_args: _Structured())
    previous = {
        "category": "耳机",
        "budget": 300,
        "brand": "漫步者",
        "color": "白色",
        "requirements": "主动降噪",
        "platform": "jd",
    }

    result = await clarify.clarify_node({
        "messages": [HumanMessage("我想买拍照手机")],
        "preferences": previous,
        "profile_preferences": {},
        "hop_count": 0,
    })

    merged = merge_preferences(previous, result["preferences"])
    assert merged == {"category": "手机"}
    assert result["trace_preferences"] == merged
    assert result["needs_action"] == "clarify"
    assert "预算" in result["clarify_question"]
