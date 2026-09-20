"""降级策略：单源失败标注「暂不可比」，全源失败退主库价。

设计决策：
- 单源熔断 → 该平台报价打 degraded 标记（不可比），其余源照常出结果，推荐走存活源；
- 全源失败（一条可用报价都没有）→ 回退 Postgres 主库价（M1 catalog 固化的价格），
  打 master_fallback 标记，让前端/推荐明确「已退主库价」而非假装实时比价结果；
- 主库价无券/物流，退避用保守默认（运费 0、时效 3 天），仅作兜底展示，不参与造假。
"""
from app.services import catalog

MASTER_ETA_DEFAULT = 3
MASTER_SHIPPING_DEFAULT = 0.0


def degrade_quote(sku_id: str, platform: str, reason: str) -> dict:
    """构造一条「不可比」的降级标记报价（不参与最优解计算，供前端展示）。"""
    return {"sku_id": sku_id, "platform": platform, "degraded": True, "reason": reason}


async def fallback_to_master(candidates: list[dict]) -> list[dict]:
    """全源挂时的兜底：按 sku_id 回主库价（无券、保守物流）。"""
    quotes: list[dict] = []
    for c in candidates:
        info = await catalog.check_stock_price(c["sku_id"])
        if info is None or not info["in_stock"]:
            continue
        quotes.append({
            "sku_id": c["sku_id"],
            "platform": "master",  # 非真实平台，代表主库价退避
            "price": info["price"],
            "coupon": None,
            "eta_days": MASTER_ETA_DEFAULT,
            "shipping_fee": MASTER_SHIPPING_DEFAULT,
            "master_fallback": True,
        })
    return quotes