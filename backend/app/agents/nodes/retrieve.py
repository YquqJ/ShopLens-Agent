"""检索节点：按偏好召回候选商品。

真实导购路径通过值得买 MCP 召回商品，推荐节点只基于值得买返回字段做比较。
"""
import json
import math

from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.agents.nodes.memory import build_effective_preferences
from app.services.product_evidence import build_zdm_evidence

_TOP_K = 5
_MALL_IDS = {
    "jd": 183, "京东": 183,
    "taobao": 243, "淘宝": 243, "淘宝精选": 243,
    "pdd": 8645, "拼多多": 8645,
}

# 真实源查询文本的字段顺序。
_SEMANTIC_ORDER = ("gender", "color", "style", "material", "category", "scene", "fit")


def _build_real_query(prefs: dict) -> str:
    """真实源查询文本：保留功能描述，同时带上可表达的结构化偏好。"""
    parts = []
    if prefs.get("requirements"):
        parts.append(str(prefs["requirements"]))
    parts.extend(str(prefs[key]) for key in _SEMANTIC_ORDER if prefs.get(key))
    return " ".join(parts)


def _find_real_search_tool(tools: dict):
    """按工具名和描述发现官方 Haina MCP 的商品搜索工具。"""
    preferred = (
        tools.get("haina_product_search_pro")
        or tools.get("haina_search_products")
    )
    if preferred is not None:
        return preferred
    for name, tool in tools.items():
        lowered = name.lower()
        description = str(getattr(tool, "description", "")).lower()
        if "search" in lowered and any(word in lowered + description for word in ("product", "商品")):
            return tool
    return None


def _real_items(result) -> list[dict]:
    """统一官方 Haina MCP 与旧本地 wrapper 的返回结构。"""
    if isinstance(result, dict):
        error_code = result.get("error_code")
        if error_code not in (None, 0, "0"):
            message = result.get("error_msg") or "未知错误"
            raise RuntimeError(f"值得买 MCP 搜索失败（{error_code}）：{message}")
        data = result.get("data")
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and isinstance(data.get("rows"), list):
            items = data["rows"]
        else:
            items = result.get("items")
        if isinstance(items, list) and all(isinstance(item, dict) for item in items):
            return items
    elif isinstance(result, list) and all(isinstance(item, dict) for item in result):
        return result
    raise RuntimeError("值得买 MCP 商品列表响应格式异常")


def build_real_search_payload(prefs: dict, *, query: str | None = None) -> dict:
    """Build the supported Haina search filters without guessing unknown mall IDs."""
    payload: dict = {
        "product_query": query if query is not None else _build_real_query(prefs),
        "size": _TOP_K,
    }
    if prefs.get("category"):
        payload["category_name"] = prefs["category"]
    if prefs.get("brand"):
        payload["brand_name"] = prefs["brand"]
    budget = prefs.get("budget")
    try:
        budget_number = float(budget)
    except (TypeError, ValueError):
        budget_number = math.nan
    if math.isfinite(budget_number) and budget_number >= 0:
        payload["max_price"] = int(budget_number)
    platform = str(prefs.get("platform") or "").strip()
    mall_id = _MALL_IDS.get(platform.lower(), _MALL_IDS.get(platform))
    if mall_id is not None:
        payload["mall_id"] = mall_id
    return payload


def _unwrap_mcp_result(result):
    """兼容 LangChain MCP 返回的 text content、JSON 字符串和已解析对象。"""
    if (
        isinstance(result, list)
        and result
        and isinstance(result[0], dict)
        and isinstance(result[0].get("text"), str)
    ):
        return json.loads(result[0]["text"])
    if isinstance(result, str):
        return json.loads(result)
    return result


def _zhidemai_to_candidates(items: list[dict]) -> list[dict]:
    """Convert actual Haina results to product facts with stable source evidence."""
    candidates: list[dict] = []
    for it in items:
        sku_id = f"ZDM{len(candidates) + 1:02d}"
        evidence = build_zdm_evidence(it, sku_id)
        values = {fact["field"]: fact["value"] for fact in evidence}
        price = values.get("page_price")
        title = values.get("title")
        if price is None or not title:
            continue
        picture = values.get("picture") or ""
        attributes = {
            label: str(values[field])
            for field, label in (("brand", "品牌"), ("category", "品类"), ("mall", "商城"), ("promotion", "促销"))
            if values.get(field) not in (None, "")
        }
        candidates.append({
            "sku_id": sku_id,
            "external_product_id": values.get("external_product_id"),
            "title": title,
            "category": values.get("category"),
            "brand": values.get("brand"),
            "mall": values.get("mall"),
            "page_price": price,
            "good_comments_share": values.get("good_comments_share"),
            "price_tags": values.get("price_tags"),
            "promotion": values.get("promotion"),
            "picture": picture,
            "image_url": picture,
            "url": values.get("url") or "",
            "attributes": attributes,
            "price": price,
            "stock": 1,
            "score": 1.0,
            "evidence": evidence,
        })
    return candidates


async def retrieve_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    prefs = build_effective_preferences(
        state.get("preferences") or {}, state.get("profile_preferences") or {},
    )

    # 值海纳真实源：query 优先按偏好拼，空则退回最新用户原话。
    query = _build_real_query(prefs) or (
        state["messages"][-1].content if state.get("messages") else ""
    )
    tools = (config or {}).get("configurable", {}).get("mcp_tools", {})
    search_tool = _find_real_search_tool(tools)
    if search_tool is None:
        raise RuntimeError("值得买 MCP 未加载：请配置 ZDM_API_KEY")
    payload = build_real_search_payload(prefs, query=query)
    result = _unwrap_mcp_result(await search_tool.ainvoke(payload))
    items = _real_items(result)
    candidates = _zhidemai_to_candidates(items)
    product_evidence = {candidate["sku_id"]: candidate["evidence"] for candidate in candidates}
    return {"candidates": candidates, "product_evidence": product_evidence, "zdm_mode": True}
