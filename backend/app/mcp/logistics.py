"""物流时效仿真源（FastMCP）：按 SKU × 平台确定性返回时效/运费。"""
from mcp.server.fastmcp import FastMCP

from app.mcp import _sim

mcp = FastMCP("logistics")


@mcp.tool()
def get_delivery(sku_id: str, platform: str) -> dict:
    """查询某平台该 SKU 的物流时效与运费。

    Args:
        sku_id: 商品 SKU 编号，如 SKU0001。
        platform: 平台，jd / taobao / pdd。
    """
    if _sim.should_fail("logistics"):
        raise RuntimeError("物流源暂时不可用（模拟下游故障）")
    rng = _sim.rng_for(sku_id, platform)
    eta_days = rng.randint(1, 5)
    shipping_fee = 0 if rng.random() < 0.6 else round(rng.uniform(6, 18), 2)
    return {"sku_id": sku_id, "platform": platform, "eta_days": eta_days, "shipping_fee": shipping_fee}


if __name__ == "__main__":
    mcp.run()