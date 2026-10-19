"""Optional knowledge-graph enrichment for verified product candidates."""
import asyncio
import json
import re
from urllib.parse import quote

from langchain_core.runnables import RunnableConfig

from app.agents.nodes.memory import build_effective_preferences
from app.agents.state import AgentState

_KG_MARKERS = ("knowledge graph", "knowledge_graph", "knowledge-graph", "entity", "实体", "知识图谱", "_kg_")
_QUERY_ARGUMENTS = {
    "query", "q", "search_query", "search_term", "search_text", "query_text",
    "keyword", "entity_query", "entity_name", "product_query",
}
_TITLE_ARGUMENTS = {"title", "product_name"}
_MODEL_ARGUMENTS = {"model", "model_name"}
_LIMIT_ARGUMENTS = {"limit", "top_k", "max_results", "size", "k"}
_BLOCKED_PATH_PARTS = ("article", "review", "comment")
_ROOT_METADATA_FIELDS = {"id", "entity_id", "score", "distance", "confidence", "type", "source"}


def _normalize_label(value) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").casefold())


def _entity_name(entity: dict) -> str:
    return str(
        entity.get("entity_name") or entity.get("name") or entity.get("product_name")
        or entity.get("title") or ""
    ).strip()


def _entity_matches(candidate: dict, entity: dict) -> int:
    candidate_brand = _normalize_label(candidate.get("brand"))
    candidate_title = _normalize_label(candidate.get("model") or candidate.get("title"))
    entity_brand_value = entity.get("brand") or entity.get("brand_name")
    entity_brand = _normalize_label(entity_brand_value)
    name = _normalize_label(_entity_name(entity))
    if not candidate_title or not name:
        return 0
    if candidate_brand:
        if entity_brand and entity_brand != candidate_brand:
            return 0
        if not entity_brand and candidate_brand not in name:
            return 0

    candidate_model = candidate_title.replace(candidate_brand, "", 1) if candidate_brand else candidate_title
    entity_model_value = entity.get("model") or entity.get("model_name")
    entity_model = _normalize_label(entity_model_value) if entity_model_value else name
    if entity_brand:
        entity_model = entity_model.replace(entity_brand, "", 1)
    if candidate_model == entity_model or candidate_title == name:
        return 2
    if candidate_model and entity_model and candidate_model in entity_model:
        return 1
    return 0


def match_kg_entity(candidate: dict, entities: list[dict]) -> dict | None:
    """Return only a unique exact entity; partial overlaps make the result ambiguous."""
    matches = [entity for entity in entities if isinstance(entity, dict) and _entity_matches(candidate, entity)]
    if len(matches) != 1 or _entity_matches(candidate, matches[0]) != 2:
        return None
    return matches[0]


def _valid_evidence_value(value) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return False
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True


def _sanitize_nested(value):
    if isinstance(value, dict):
        return {
            key: _sanitize_nested(item)
            for key, item in value.items()
            if not any(marker in str(key).casefold() for marker in _BLOCKED_PATH_PARTS)
        }
    if isinstance(value, list):
        return [_sanitize_nested(item) for item in value]
    return value


def _flatten_properties(value, prefix: str = "", depth: int = 0):
    if depth > 4 or not isinstance(value, dict):
        return
    for key, item in value.items():
        key_text = str(key).strip()
        if not key_text or any(marker in key_text.casefold() for marker in _BLOCKED_PATH_PARTS):
            continue
        if depth == 0 and key_text.casefold() in _ROOT_METADATA_FIELDS:
            continue
        path = f"{prefix}.{key_text}" if prefix else key_text
        if isinstance(item, dict):
            yield from _flatten_properties(item, path, depth + 1)
        else:
            cleaned = _sanitize_nested(item)
            if _valid_evidence_value(cleaned):
                yield path, cleaned


def normalize_kg_evidence(candidate: dict, entity: dict) -> list[dict]:
    """Keep structured entity facts with paths; article/review material is excluded."""
    sku_id = candidate["sku_id"]
    entity_name = _entity_name(entity)
    facts = []
    for field_path, value in _flatten_properties(entity):
        display_field = "entity_description" if field_path in {"description", "entity_description"} else field_path
        facts.append({
            "evidence_id": f"{sku_id}:kg:{quote(display_field, safe='._-')}",
            "sku_id": sku_id,
            "source": "haina_kg",
            "field": display_field,
            "value": value,
            "source_ref": f"{entity_name}#{field_path}",
            "entity_name": entity_name,
            "field_path": field_path,
            "kind": "description" if display_field == "entity_description" else "attribute",
        })
    return facts


def _tool_schema(tool) -> dict | None:
    schema = getattr(tool, "input_schema", None)
    if isinstance(schema, dict):
        return schema
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None:
        if hasattr(args_schema, "model_json_schema"):
            schema = args_schema.model_json_schema()
        elif hasattr(args_schema, "schema"):
            schema = args_schema.schema()
        if isinstance(schema, dict):
            return schema
    args = getattr(tool, "args", None)
    if isinstance(args, dict):
        if isinstance(args.get("properties"), dict):
            return args
        return {"type": "object", "properties": args, "required": []}
    return None


def _kg_payload(tool, candidate: dict) -> dict | None:
    schema = _tool_schema(tool)
    properties = schema.get("properties") if schema else None
    if not isinstance(properties, dict) or not properties:
        return None
    brand = str(candidate.get("brand") or "").strip()
    title = str(candidate.get("title") or "").strip()
    model = str(candidate.get("model") or title).strip()
    query = " ".join(part for part in (brand, model) if part)
    payload = {}
    for name in properties:
        lowered = name.casefold()
        if lowered in _QUERY_ARGUMENTS:
            payload[name] = query
        elif lowered in {"brand", "brand_name"} and brand:
            payload[name] = brand
        elif lowered in _TITLE_ARGUMENTS and title:
            payload[name] = title
        elif lowered in _MODEL_ARGUMENTS and model:
            payload[name] = model
        elif lowered in {"category", "category_name"} and candidate.get("category"):
            payload[name] = candidate["category"]
        elif lowered in _LIMIT_ARGUMENTS:
            payload[name] = 5
    required = schema.get("required") or []
    if not payload or any(name not in payload for name in required):
        return None
    return payload


def _find_kg_tool(tools: dict, candidate: dict):
    for name, tool in tools.items():
        description = str(getattr(tool, "description", ""))
        lowered = f"{name} {description}".casefold()
        if any(marker in lowered for marker in _KG_MARKERS) and _kg_payload(tool, candidate) is not None:
            return tool
    return None


def _unwrap_result(result):
    if isinstance(result, str):
        return json.loads(result)
    if isinstance(result, list) and result and isinstance(result[0], dict) and isinstance(result[0].get("text"), str):
        return json.loads(result[0]["text"])
    return result


def _extract_entities(result) -> list[dict]:
    value = _unwrap_result(result)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        raise ValueError("unrecognized knowledge-graph response")
    error_code = value.get("error_code")
    if error_code not in (None, 0, "0"):
        raise ValueError(f"knowledge-graph error {error_code}: {value.get('error_msg', '')}")
    for key in ("entities", "results", "items", "data"):
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            extracted = _extract_entities(nested)
            if extracted:
                return extracted
    if _entity_name(value):
        return [value]
    if any(key in value for key in ("entities", "results", "items", "data")):
        return []
    raise ValueError("unrecognized knowledge-graph response")


def _article_metadata(entity: dict) -> dict:
    links = entity.get("article_links")
    return {"article_links": links} if isinstance(links, list) and links else {}


async def knowledge_enrich_node(
    state: AgentState,
    config: RunnableConfig | None = None,
) -> dict:
    """Enrich up to three budget-eligible, identifiable candidates concurrently."""
    candidates = state.get("candidates", [])
    evidence_by_sku = {
        sku_id: list(facts)
        for sku_id, facts in (state.get("product_evidence") or {}).items()
    }
    for candidate in candidates:
        evidence_by_sku.setdefault(candidate["sku_id"], list(candidate.get("evidence") or []))
    kg_status = {candidate["sku_id"]: "skipped" for candidate in candidates}
    kg_metadata = dict(state.get("kg_metadata") or {})
    preferences = build_effective_preferences(
        state.get("preferences") or {}, state.get("profile_preferences") or {},
    )
    budget = preferences.get("budget")
    eligible = []
    for candidate in candidates:
        price = candidate.get("page_price")
        try:
            numeric_price = float(price)
        except (TypeError, ValueError):
            continue
        if budget is not None and numeric_price > float(budget):
            continue
        if not candidate.get("title") or not (candidate.get("brand") or candidate.get("model")):
            kg_status[candidate["sku_id"]] = "unmatched"
            continue
        if len(eligible) < 3:
            eligible.append(candidate)

    if not eligible:
        return {"product_evidence": evidence_by_sku, "kg_status": kg_status, "kg_metadata": kg_metadata}

    tools = (config or {}).get("configurable", {}).get("mcp_tools", {})
    tool = None
    for candidate in eligible:
        tool = _find_kg_tool(tools, candidate)
        if tool is not None:
            break
    if tool is None:
        for candidate in eligible:
            kg_status[candidate["sku_id"]] = "unavailable"
        return {"product_evidence": evidence_by_sku, "kg_status": kg_status, "kg_metadata": kg_metadata}

    semaphore = asyncio.Semaphore(3)

    async def enrich(candidate: dict):
        sku_id = candidate["sku_id"]
        payload = _kg_payload(tool, candidate)
        if payload is None:
            return sku_id, "unavailable", [], {}
        try:
            async with semaphore:
                result = await tool.ainvoke(payload)
            entities = _extract_entities(result)
        except Exception:
            return sku_id, "degraded", [], {}
        entity = match_kg_entity(candidate, entities)
        if entity is None:
            return sku_id, "unmatched", [], {}
        return sku_id, "matched", normalize_kg_evidence(candidate, entity), _article_metadata(entity)

    results = await asyncio.gather(*(enrich(candidate) for candidate in eligible))
    for sku_id, status, facts, metadata in results:
        kg_status[sku_id] = status
        if facts:
            evidence_by_sku.setdefault(sku_id, []).extend(facts)
        if metadata:
            kg_metadata[sku_id] = metadata
    return {"product_evidence": evidence_by_sku, "kg_status": kg_status, "kg_metadata": kg_metadata}
