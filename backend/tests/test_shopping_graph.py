import pytest

from app.agents.graph import build_graph
from app.agents.nodes import retrieve
from app.agents.state import initial_state


def test_shopping_graph_uses_real_product_facts_without_enrichment_node():
    nodes = set(build_graph().get_graph().nodes)

    assert {"retrieve", "recommend", "confirm", "place_order"} <= nodes
    assert "enrich_knowledge" not in nodes
    assert "load_memory" not in nodes
    assert "capture_memory" not in nodes


@pytest.mark.anyio
async def test_retrieval_without_haina_tool_fails_explicitly():
    with pytest.raises(RuntimeError, match="值得买 MCP 未加载"):
        await retrieve.retrieve_node(
            {"preferences": {"category": "手机", "budget": 3000}, "profile_preferences": {}, "messages": []},
            {"configurable": {"mcp_tools": {}}},
        )


@pytest.mark.parametrize(
    "preferences",
    [
        {"category": "显卡"},
        {"budget": 8000},
        {},
    ],
)
@pytest.mark.anyio
async def test_retrieval_requires_category_and_budget(preferences):
    with pytest.raises(ValueError, match="品类和预算"):
        await retrieve.retrieve_node(
            {"preferences": preferences, "messages": []},
            {"configurable": {"mcp_tools": {}}},
        )


def test_initial_state_has_evidence_and_comparison_fields():
    state = initial_state("买手机")
    assert state["product_evidence"] == {}
    assert state["comparison"] == {}
    assert state["order_intent_id"] is None
    assert state["turn_id"]
    assert state["recommendation_history"] == []
    assert state["order_history"] == []
    assert state["recommendation_intro"] == ""
    assert state["messages"][0].additional_kwargs["turn_id"] == state["turn_id"]
