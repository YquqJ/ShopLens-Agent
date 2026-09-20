"""拼多多比价仿真源（FastMCP）：种子报价 + 随机波动 + 可开关故障。"""
from mcp.server.fastmcp import FastMCP

from app.mcp import _sim

mcp = FastMCP("price_pdd")


@mcp.tool()
def get_quote(sku_id: str) -> dict:
    """查询拼多多平台该 SKU 的实时报价。

    Args:
        sku_id: 商品 SKU 编号，如 SKU0001。
    """
    if _sim.should_fail("pdd"):
        raise RuntimeError("拼多多比价源暂时不可用（模拟下游故障）")
    return {
        "platform": "pdd",
        "sku_id": sku_id,
        "price": _sim.quote(sku_id, "pdd"),
        "currency": "CNY",
    }


if __name__ == "__main__":
    mcp.run()