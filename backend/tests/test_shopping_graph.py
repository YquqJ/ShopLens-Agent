import pytest

from app.agents.graph import build_graph
from app.agents.nodes import retrieve
from app.agents.state import initial_state


def test_shopping_graph_contains_kg_but_not_mock_price_comparison():
    nodes = set(build_graph().get_graph().nodes)

    assert {"enrich_knowledge", "recommend", "confirm", "place_order"} <= nodes
    assert not {"compare", "normalize", "lock_price"} & nodes


@pytest.mark.anyio
async def test_real_retrieval_without_haina_tool_fails_explicitly_and_does_not_use_catalog(monkeypatch):
    called = False

    async def forbidden_search(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("catalog fallback must not run")

    monkeypatch.setattr(retrieve.catalog, "search", forbidden_search)

    with pytest.raises(RuntimeError, match="Haina 商品搜索 MCP 工具"):
        await retrieve.retrieve_node(
            {"preferences": {"category": "手机", "budget": 3000}, "profile_preferences": {}, "messages": []},
            {"configurable": {"mcp_tools": {}, "source_mode": "real"}},
        )

    assert called is False


def test_initial_state_has_evidence_and_comparison_fields():
    state = initial_state("买手机")
    assert state["product_evidence"] == {}
    assert state["kg_status"] == {}
    assert state["comparison"] == {}
    assert state["order_intent_id"] is None
    assert state["turn_id"]
    assert state["recommendation_history"] == []
    assert state["order_history"] == []
    assert state["recommendation_intro"] == ""
    assert state["messages"][0].additional_kwargs["turn_id"] == state["turn_id"]
