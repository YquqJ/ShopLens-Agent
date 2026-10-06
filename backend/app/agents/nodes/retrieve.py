"""检索节点：按偏好召回候选商品。

两条路径（由请求级 source_mode 选择）：
- 默认：Chroma 向量检索（店铺种子目录）；
- 值海纳模式：通过 zhidemai MCP 工具召回真实商品（方案 B），
  直接把真实报价构造为 normalized 结构，供 recommend 直接消费——
  跳过 compare/normalize（真实源无跨平台 sku 对齐，且不可喂仿真比价源，否则价格现编）。
"""
import json

from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.agents.nodes.memory import build_effective_preferences
from app.core.config import settings
from app.services import catalog

_TOP_K = 5

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


def _zhidemai_to_normalized(items: list[dict]) -> list[dict]:
    """值海纳真实商品摘要 → normalize 同构的 normalized 列表（单商城报价）。

    值海纳是聚合源，无跨平台报价，故 quotes/best 只含「该商品当前商城的真实报价」，
    eta_days=0（无物流时效字段）；价格取 services.zhidemai 规整后的 page_price 数值。
    """
    normalized: list[dict] = []
    for i, it in enumerate(items, 1):
        price = it.get("price")
        if price is None:  # 无报价商品跳过，不编造价格
            continue
        mall = it.get("mall") or "未知商城"
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
        attributes = {
            k: str(v)
            for k, v in {
                "品牌": it.get("brand"),
                "品类": it.get("category"),
                "商城": mall,
                "促销": it.get("promotion"),
            }.items()
            if v not in (None, "")
        }
        normalized.append({
            "sku_id": f"ZDM{i:02d}",
            "title": it.get("title"),
            "category": it.get("category"),
            "attributes": attributes,
            "image_url": it.get("picture") or "",
            "url": it.get("url") or "",
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
        search_tool = tools.get("zhidemai_search_real_products")
        if search_tool is None:
            raise RuntimeError("真实数据源未加载：缺少 zhidemai_search_real_products MCP 工具")
        payload: dict = {"product_query": query, "size": _TOP_K}
        if prefs.get("category"):
            payload["category_name"] = prefs["category"]
        if prefs.get("budget") not in (None, ""):
            payload["max_price"] = int(float(prefs["budget"]))
        result = _unwrap_mcp_result(await search_tool.ainvoke(payload))
        items = result.get("items", []) if isinstance(result, dict) else result
        if not isinstance(items, list):
            raise RuntimeError("值得买 MCP 返回了无法解析的商品列表")
        normalized = _zhidemai_to_normalized(items)
        # candidates 仅作 trace 计数与结构复用（sku_id 为 ZDM 合成 id）
        candidates = [
            {
                "sku_id": n["sku_id"],
                "title": n["title"],
                "category": n["category"],
                "attributes": n["attributes"],
                "price": n["final_price"],
                "stock": 1,
                "image_url": n.get("image_url", ""),
                "url": n.get("url", ""),
                "score": 1.0,
            }
            for n in normalized
        ]
        return {"candidates": candidates, "normalized": normalized, "zdm_mode": True}

    filters = _build_filters(prefs)
    semantic_q = _build_semantic_query(prefs)
    candidates = await catalog.search(semantic_q, top_k=_TOP_K, filters=filters)
    if not candidates:
        # 硬过滤无命中时退回纯语义，保住「总是有结果」的体验
        candidates = await catalog.search(semantic_q, top_k=_TOP_K)
    # 仿真源只用于展示默认商品卡片，不向前端暴露图片地址；真实源图片仍在 normalized 中保留。
    candidates = [dict(candidate, image_url="") for candidate in candidates]
    return {"candidates": candidates, "zdm_mode": False}
