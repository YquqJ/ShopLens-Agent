"""跨源 SKU 归一 + 最优解计算（业务逻辑，节点编排见 agents/nodes/normalize.py）。

设计决策：
- v0 的 MCP 源按我方 canonical sku_id 报价，故「归一」= 按 sku_id 把三平台报价聚成一条；
  真实跨源的标题/属性相似对齐（不同平台不同 sku id）留到需要时再加；
- 到手价 = 价 − 券(满 threshold 才减) + 运费；综合成本再叠加时效折算（每等 1 天折合 ¥10）；
- 最优解 = 综合成本最低的平台；权重 TIME_VALUE_PER_DAY 沉淀 docs/DESIGN_NOTES。
"""

# 时效折算权重（元/天）
TIME_VALUE_PER_DAY = 10.0


def _discount(price: float, coupon: dict | None) -> float:
    """券抵扣：满 threshold 才生效，否则 0。"""
    if not coupon:
        return 0.0
    return coupon["amount"] if price >= coupon["threshold"] else 0.0


def normalize(quotes: list[dict], candidates: list[dict]) -> list[dict]:
    """把三平台原始报价按 sku_id 归一，附最优解，按综合成本升序返回。"""
    meta = {c["sku_id"]: c for c in candidates}
    by_sku: dict[str, dict] = {}
    for q in quotes:
        sku = q["sku_id"]
        item = by_sku.get(sku)
        if item is None:
            m = meta.get(sku, {})
            item = by_sku[sku] = {
                "sku_id": sku,
                "title": m.get("title", sku),
                "category": m.get("category"),
                "attributes": m.get("attributes", {}),
                "quotes": [],
            }
        discount = round(_discount(q["price"], q["coupon"]), 2)
        final_price = round(q["price"] - discount + q["shipping_fee"], 2)
        total_cost = round(final_price + q["eta_days"] * TIME_VALUE_PER_DAY, 2)
        item["quotes"].append({
            "platform": q["platform"],
            "price": q["price"],
            "coupon": q["coupon"],
            "discount": discount,
            "shipping_fee": q["shipping_fee"],
            "eta_days": q["eta_days"],
            "final_price": final_price,
            "total_cost": total_cost,
        })
    result: list[dict] = []
    for item in by_sku.values():
        best = min(item["quotes"], key=lambda x: x["total_cost"])
        item["best"] = best
        item["final_price"] = best["final_price"]
        result.append(item)
    result.sort(key=lambda x: x["best"]["total_cost"])
    return result