"""优惠券仿真源（FastMCP）：按 SKU × 平台确定性返回可用券。"""
from mcp.server.fastmcp import FastMCP

from app.mcp import _sim

mcp = FastMCP("coupon")


@mcp.tool()
def get_coupons(sku_id: str, platform: str) -> dict:
    """查询某平台该 SKU 可用的优惠券。

    Args:
        sku_id: 商品 SKU 编号，如 SKU0001。
        platform: 平台，jd / taobao / pdd。
    """
    if _sim.should_fail("coupon"):
        raise RuntimeError("优惠券源暂时不可用（模拟下游故障）")
    price = _sim.market_price(sku_id)
    rng = _sim.rng_for(sku_id, platform)
    if rng.random() < 0.25:
        coupon = None  # 小概率无券
    else:
        amount = round(price * rng.uniform(0.03, 0.10), 2)
        threshold = round(price * rng.uniform(0.3, 0.7), 2)
        coupon = {"amount": amount, "threshold": threshold}
    return {"sku_id": sku_id, "platform": platform, "coupon": coupon}


if __name__ == "__main__":
    mcp.run()