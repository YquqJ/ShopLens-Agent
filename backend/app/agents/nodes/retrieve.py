"""检索节点：按偏好召回候选商品。

两条路径（由 SHOPLENS_ZHIDEMAI_ENABLED 开关选）：
- 默认：Chroma 向量检索（店铺种子目录）；
- 值海纳模式：直连海纳开放平台召回真实商品（方案 B），
  直接把真实报价构造为 normalized 结构，供 recommend 直接消费——
  跳过 compare/normalize（真实源无跨平台 sku 对齐，且不可喂仿真比价源，否则价格现编）。
"""
from app.agents.state import AgentState
from app.agents.nodes.memory import build_effective_preferences
from app.core.config import settings
from app.services import catalog
from app.services import zhidemai as zhidemai_service

_TOP_K = 5


def _build_query(prefs: dict) -> str:
    """当前会话优先，长期偏好只在对应当前字段缺失时补入查询。"""
    parts = [str(prefs[k]) for k in ("color", "style", "material", "category") if prefs.get(k)]
    if not prefs.get("color"):
        parts.extend(str(v) for v in prefs.get("preferred_colors", []) if v)
    if not prefs.get("style"):
        parts.extend(str(v) for v in prefs.get("preferred_styles", []) if v)
    if not prefs.get("material"):
        parts.extend(str(v) for v in prefs.get("preferred_materials", []) if v)
    return " ".join(parts) if parts else str(prefs.get("category", ""))


def _zhidemai_enabled() -> bool:
    """真实源开关：显式开启且已配 ZDM_API_KEY 才启用。

    从 settings 读（.env 经 pydantic-settings 只进 settings，不进 os.environ）。
    """
    return bool(settings.SHOPLENS_ZHIDEMAI_ENABLED) and bool(settings.ZDM_API_KEY)


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
            "quotes": [quote],
            "best": quote,
            "cost_best": quote,
            "best_strategy": "lowest_total_cost",
            "final_price": price,
        })
    return normalized


async def retrieve_node(state: AgentState) -> dict:
    prefs = build_effective_preferences(
        state.get("preferences") or {}, state.get("profile_preferences") or {},
    )

    if _zhidemai_enabled():
        # 值海纳真实源：query 优先按偏好拼，空则退回最新用户原话
        query = _build_query(prefs) or (
            state["messages"][-1].content if state.get("messages") else ""
        )
        items = await zhidemai_service.search_real_products(query, size=_TOP_K)
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
                "image_url": "",
                "score": 1.0,
            }
            for n in normalized
        ]
        return {"candidates": candidates, "normalized": normalized, "zdm_mode": True}

    candidates = await catalog.search(_build_query(prefs), top_k=_TOP_K)
    return {"candidates": candidates, "zdm_mode": False}
