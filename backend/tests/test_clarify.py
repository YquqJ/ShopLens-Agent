import pytest
from langchain_core.messages import HumanMessage

from app.agents.nodes import clarify
from app.agents.state import merge_preferences


def test_preferences_are_limited_to_common_clothing_and_digital_fields():
    assert set(clarify.Preferences.model_fields) == {
        "category", "budget", "requirements", "gender", "color", "brand", "use_case",
    }


def test_clothing_gender_is_not_required_for_other_product_categories():
    assert clarify._missing({"category": "手机", "budget": 3000}) == []


def test_digital_category_has_one_optional_question_without_becoming_required():
    assert clarify._optional_question({"category": "手机", "budget": 5000}) == "有偏好的品牌吗？主要用于拍照、游戏，还是日常使用？"
    assert clarify._missing({"category": "手机", "budget": 5000}) == []


def test_clothing_category_has_one_optional_question_without_becoming_required():
    assert clarify._optional_question({"category": "外套", "budget": 500}) == "更偏向男款、女款还是中性款？颜色有偏好吗？"


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
        "use_case": "游戏",
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


@pytest.mark.anyio
async def test_digital_request_asks_once_but_still_proceeds_to_shopping(monkeypatch):
    class _Structured:
        async def ainvoke(self, _messages):
            return clarify.ClarifyResult(
                needs_clarification=False,
                preferences=clarify.Preferences(category="手机", budget=5000),
                clarify_question="",
            )

    monkeypatch.setattr(clarify, "get_structured_llm", lambda *_args: _Structured())
    result = await clarify.clarify_node({
        "messages": [HumanMessage("我要买个5000元的手机")],
        "preferences": {},
        "hop_count": 0,
        "optional_clarify_asked": False,
    })

    assert result["needs_action"] == "shopping"
    assert result["clarify_question"] == "有偏好的品牌吗？主要用于拍照、游戏，还是日常使用？"
    assert result["optional_clarify_asked"] is True
    assert len(result["messages"]) == 1


@pytest.mark.anyio
async def test_optional_question_is_not_repeated_after_user_answers(monkeypatch):
    class _Structured:
        async def ainvoke(self, _messages):
            return clarify.ClarifyResult(
                needs_clarification=False,
                preferences=clarify.Preferences(brand="小米", use_case="拍照"),
                clarify_question="",
            )

    monkeypatch.setattr(clarify, "get_structured_llm", lambda *_args: _Structured())
    result = await clarify.clarify_node({
        "messages": [HumanMessage("品牌小米，主要拍照")],
        "preferences": {"category": "手机", "budget": 5000},
        "hop_count": 1,
        "optional_clarify_asked": True,
    })

    assert result["needs_action"] == "shopping"
    assert result["clarify_question"] == ""
    assert result["optional_clarify_asked"] is True


def test_initial_state_can_carry_optional_question_status_between_requests():
    from app.agents.state import initial_state

    assert initial_state("继续", optional_clarify_asked=True)["optional_clarify_asked"] is True
    assert initial_state("新需求", reset_preferences=True)["optional_clarify_asked"] is False
