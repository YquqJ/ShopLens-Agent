import pytest

from app.agents.state import initial_state
from app.infra.protect import SourceError


def _knowledge():
    from app.agents.nodes import knowledge

    return knowledge


def test_ambiguous_kg_entities_are_not_attached():
    knowledge = _knowledge()
    entities = [
        {"entity_name": "Brand X Model A", "brand": "Brand X", "model": "Model A"},
        {"entity_name": "Brand X Model A Pro", "brand": "Brand X", "model": "Model A Pro"},
    ]
    candidate = {"brand": "Brand X", "title": "Model A"}

    assert knowledge.match_kg_entity(candidate, entities) is None


def test_kg_entity_with_conflicting_brand_is_not_attached():
    knowledge = _knowledge()
    assert knowledge.match_kg_entity(
        {"brand": "Brand X", "title": "Model A"},
        [{"entity_name": "Brand Y Model A", "brand": "Brand Y", "model": "Model A"}],
    ) is None


def test_article_links_do_not_become_comparison_facts():
    knowledge = _knowledge()
    entity = {
        "entity_name": "Brand X Model A",
        "brand": "Brand X",
        "model": "Model A",
        "entity_id": "KG-17",
        "confidence": 0.99,
        "attributes": {"battery_capacity": "5000mAh"},
        "article_links": [{"title": "Review"}],
    }

    facts = knowledge.normalize_kg_evidence(
        {"sku_id": "ZDM01", "brand": "Brand X", "title": "Model A"},
        entity,
    )

    assert any(fact["field"] == "attributes.battery_capacity" for fact in facts)
    assert all("article" not in fact["field"] and "review" not in fact["field"] for fact in facts)
    assert all(fact["field"] not in {"entity_id", "confidence"} for fact in facts)


@pytest.mark.anyio
async def test_kg_source_failure_degrades_without_discarding_product_evidence():
    knowledge = _knowledge()
    product_evidence = [{
        "evidence_id": "ZDM01:zdm:page_price",
        "sku_id": "ZDM01",
        "source": "zhidemai",
        "field": "page_price",
        "value": 399,
        "source_ref": "page_price",
    }]

    class _Tool:
        name = "haina_knowledge_graph_search"
        description = "Search knowledge graph entities"
        args = {"query": {"type": "string"}}

        async def ainvoke(self, _payload):
            raise SourceError("haina_kg", "circuit open")

    state = initial_state("比较 Brand X Model A")
    state.update({
        "candidates": [{
            "sku_id": "ZDM01", "title": "Brand X Model A", "brand": "Brand X",
            "page_price": 399, "evidence": product_evidence,
        }],
        "product_evidence": {"ZDM01": product_evidence},
    })

    result = await knowledge.knowledge_enrich_node(
        state,
        {"configurable": {"mcp_tools": {_Tool.name: _Tool()}}},
    )

    assert result["kg_status"]["ZDM01"] == "degraded"
    assert result["product_evidence"] == {"ZDM01": product_evidence}


@pytest.mark.anyio
async def test_kg_success_adds_matched_facts_and_keeps_article_links_as_metadata():
    knowledge = _knowledge()

    class _Tool:
        name = "haina_knowledge_graph_search"
        description = "Search knowledge graph entities"
        args = {"query": {"type": "string"}, "limit": {"type": "integer"}}

        async def ainvoke(self, payload):
            assert payload == {"query": "Brand X Model A", "limit": 5}
            return {"entities": [{
                "entity_name": "Brand X Model A",
                "brand": "Brand X",
                "model": "Model A",
                "properties": {"battery_capacity": "5000mAh"},
                "article_links": [{"title": "Review", "url": "https://example.test/review"}],
            }]}

    product_fact = {
        "evidence_id": "ZDM01:zdm:page_price",
        "sku_id": "ZDM01",
        "source": "zhidemai",
        "field": "page_price",
        "value": 399,
        "source_ref": "page_price",
    }
    state = initial_state("比较 Brand X Model A")
    state.update({
        "preferences": {"budget": 500},
        "candidates": [{
            "sku_id": "ZDM01", "title": "Model A", "brand": "Brand X", "page_price": 399,
        }],
        "product_evidence": {"ZDM01": [product_fact]},
    })

    result = await knowledge.knowledge_enrich_node(
        state,
        {"configurable": {"mcp_tools": {_Tool.name: _Tool()}}},
    )

    assert result["kg_status"]["ZDM01"] == "matched"
    assert result["product_evidence"]["ZDM01"][0] == product_fact
    assert any(
        fact["field"] == "properties.battery_capacity" and fact["value"] == "5000mAh"
        for fact in result["product_evidence"]["ZDM01"]
    )
    assert result["kg_metadata"]["ZDM01"]["article_links"][0]["title"] == "Review"


@pytest.mark.anyio
async def test_missing_kg_tool_marks_candidates_unavailable():
    knowledge = _knowledge()
    state = initial_state("比较商品")
    state["candidates"] = [{
        "sku_id": "ZDM01", "title": "Model A", "brand": "Brand X", "page_price": 10,
    }]

    result = await knowledge.knowledge_enrich_node(
        state,
        {"configurable": {"mcp_tools": {}}},
    )

    assert result["kg_status"] == {"ZDM01": "unavailable"}


@pytest.mark.anyio
async def test_kg_error_envelope_is_reported_as_degraded():
    knowledge = _knowledge()

    class _Tool:
        name = "haina_knowledge_graph_search"
        description = "Search knowledge graph entities"
        args = {"query": {"type": "string"}}

        async def ainvoke(self, _payload):
            return {"error_code": 503, "error_msg": "temporarily unavailable"}

    state = initial_state("比较商品")
    state["candidates"] = [{
        "sku_id": "ZDM01", "title": "Model A", "brand": "Brand X", "page_price": 10,
    }]

    result = await knowledge.knowledge_enrich_node(
        state,
        {"configurable": {"mcp_tools": {_Tool.name: _Tool()}}},
    )

    assert result["kg_status"] == {"ZDM01": "degraded"}


@pytest.mark.anyio
async def test_unsupported_kg_schema_is_not_called():
    knowledge = _knowledge()

    class _Tool:
        name = "haina_knowledge_graph_search"
        description = "Search knowledge graph entities"
        args = {"tenant_id": {"type": "string"}}
        calls = 0

        async def ainvoke(self, _payload):
            self.calls += 1

    tool = _Tool()
    state = initial_state("比较商品")
    state["candidates"] = [{
        "sku_id": "ZDM01", "title": "Model A", "brand": "Brand X", "page_price": 10,
    }]

    result = await knowledge.knowledge_enrich_node(
        state,
        {"configurable": {"mcp_tools": {tool.name: tool}}},
    )

    assert result["kg_status"] == {"ZDM01": "unavailable"}
    assert tool.calls == 0


@pytest.mark.anyio
async def test_discovery_skips_unsupported_kg_tool_and_uses_supported_schema():
    knowledge = _knowledge()

    class _UnsupportedTool:
        name = "haina_knowledge_graph_legacy"
        description = "Search knowledge graph entities"
        args = {"tenant_id": {"type": "string"}}

        async def ainvoke(self, _payload):
            raise AssertionError("unsupported tool must not be called")

    class _SupportedTool:
        name = "haina_knowledge_graph_search"
        description = "Search knowledge graph entities"
        args = {"query": {"type": "string"}}

        async def ainvoke(self, payload):
            assert payload == {"query": "Brand X Model A"}
            return {"entities": [{
                "entity_name": "Brand X Model A",
                "brand": "Brand X",
                "model": "Model A",
            }]}

    state = initial_state("比较商品")
    state["candidates"] = [{
        "sku_id": "ZDM01", "title": "Model A", "brand": "Brand X", "page_price": 10,
    }]

    result = await knowledge.knowledge_enrich_node(
        state,
        {"configurable": {"mcp_tools": {
            _UnsupportedTool.name: _UnsupportedTool(),
            _SupportedTool.name: _SupportedTool(),
        }}},
    )

    assert result["kg_status"] == {"ZDM01": "matched"}
