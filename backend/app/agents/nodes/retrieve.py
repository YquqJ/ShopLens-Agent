"""检索节点：按偏好召回候选商品。

真实导购路径通过值得买 MCP 召回商品，并保留 normalized 兼容结构；
随后由知识图谱节点增强证据，推荐节点只基于服务端事实做比较。
"""
import json
import math

from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.agents.nodes.memory import build_effective_preferences
from app.core.config import settings
from app.services import catalog
from app.services.product_evidence import build_zdm_evidence

_TOP_K = 5
_MALL_IDS = {
    "jd": 183, "京东": 183,
    "taobao": 243, "淘宝": 243, "淘宝精选": 243,
    "pdd": 8645, "拼多多": 8645,
}

# 闭集硬约束字段（进 Chroma where）：品类/颜色/风格/材质/场景/版型/性别
_CLOSED_FIELDS = ("category", "color", "style", "material", "scene", "fit", "gender")
# 语义召回文本的字段顺序（回退拼接用，保持「颜色/风格/材质/品类」的旧观感）
_SEMANTIC_ORDER = ("gender", "color", "style", "material", "category", "scene", "fit")


def _build_filters(prefs: dict) -> dict:
    """闭集硬约束 → Chroma where：会话字段优先（单值），缺失才用长期偏好（列表 → $in）。"""
    filters: dict = {}
    for key in _CLOSED_FIELDS:
        value = prefs.get(key)
        if value not in (None, "", []):
            filters[key] = value
    for key, preferred in (
        ("color", "preferred_colors"),
        ("style", "preferred_styles"),
        ("material", "preferred_materials"),
        ("gender", "preferred_genders"),
    ):
        if key not in filters and prefs.get(preferred):
            filters[key] = prefs[preferred]
    return filters


def _build_semantic_query(prefs: dict) -> str:
    """语义召回文本：优先功能词 requirements，空则回退到闭集字段拼接。"""
    if prefs.get("requirements"):
        return str(prefs["requirements"])
    parts = [str(prefs[k]) for k in _SEMANTIC_ORDER if prefs.get(k)]
    return " ".join(parts) if parts else str(prefs.get("category", ""))


def _build_real_query(prefs: dict) -> str:
    """真实源查询文本：保留功能描述，同时带上可表达的结构化偏好。"""
    parts = []
    if prefs.get("requirements"):
        parts.append(str(prefs["requirements"]))
    parts.extend(str(prefs[key]) for key in _SEMANTIC_ORDER if prefs.get(key))
    return " ".join(parts)


def _zhidemai_enabled() -> bool:
    """真实源开关：显式开启且已配 ZDM_API_KEY 才启用。

    从 settings 读（.env 经 pydantic-settings 只进 settings，不进 os.environ）。
    """
    return bool(settings.SHOPLENS_ZHIDEMAI_ENABLED) and bool(settings.ZDM_API_KEY)


def _find_real_search_tool(tools: dict):
    """按工具名和描述发现官方 Haina MCP 的商品搜索工具。"""
    preferred = (
        tools.get("haina_product_search_pro")
        or tools.get("haina_search_products")
        or tools.get("zhidemai_search_real_products")
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
        data = result.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            return data["rows"]
        items = result.get("items")
        if isinstance(items, list):
            return items
    return result if isinstance(result, list) else []


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


def _source_mode(config: RunnableConfig | None) -> str:
    """请求显式指定模式时优先使用；旧调用未传时兼容环境变量开关。"""
    configurable = (config or {}).get("configurable", {})
    mode = configurable.get("source_mode")
    if mode in {"mock", "real"}:
        return mode
    return "real" if _zhidemai_enabled() else "mock"


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


def _zhidemai_to_normalized(items: list[dict]) -> list[dict]:
    """Temporary adapter for the legacy recommendation node during graph migration."""
    normalized: list[dict] = []
    for candidate in _zhidemai_to_candidates(items):
        price = candidate["page_price"]
        mall = candidate["mall"] or "未知商城"
        quote = {
            "platform": mall,
            "price": price,
            "coupon": None,
            "discount": 0,
            "shipping_fee": 0,
            "eta_days": 0,
            "final_price": price,
            "total_cost": price,
        }
        normalized.append({
            "sku_id": candidate["sku_id"],
            "title": candidate["title"],
            "category": candidate["category"],
            "attributes": candidate["attributes"],
            "image_url": candidate["image_url"],
            "url": candidate["url"],
            "quotes": [quote],
            "best": quote,
            "final_price": price,
        })
    return normalized


async def retrieve_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    prefs = build_effective_preferences(
        state.get("preferences") or {}, state.get("profile_preferences") or {},
    )

    if _source_mode(config) == "real":
        # 值海纳真实源：query 优先按偏好拼，空则退回最新用户原话
        query = _build_real_query(prefs) or (
            state["messages"][-1].content if state.get("messages") else ""
        )
        tools = (config or {}).get("configurable", {}).get("mcp_tools", {})
        search_tool = _find_real_search_tool(tools)
        if search_tool is None:
            raise RuntimeError("真实数据源未加载：未发现 Haina 商品搜索 MCP 工具")
        payload = build_real_search_payload(prefs, query=query)
        result = _unwrap_mcp_result(await search_tool.ainvoke(payload))
        items = _real_items(result)
        if not items:
            raise RuntimeError("值得买 MCP 返回了无法解析的商品列表")
        candidates = _zhidemai_to_candidates(items)
        normalized = _zhidemai_to_normalized(items)
        product_evidence = {candidate["sku_id"]: candidate["evidence"] for candidate in candidates}
        return {
            "candidates": candidates,
            "product_evidence": product_evidence,
            "normalized": normalized,
            "zdm_mode": True,
        }

    filters = _build_filters(prefs)
    semantic_q = _build_semantic_query(prefs)
    candidates = await catalog.search(semantic_q, top_k=_TOP_K, filters=filters)
    if not candidates:
        # 硬过滤无命中时退回纯语义，保住「总是有结果」的体验
        candidates = await catalog.search(semantic_q, top_k=_TOP_K)
    # 仿真源只用于展示默认商品卡片，不向前端暴露图片地址；真实源图片仍在 normalized 中保留。
    candidates = [dict(candidate, image_url="") for candidate in candidates]
    return {"candidates": candidates, "zdm_mode": False}
