"""本地订单查询 FastMCP stdio 服务。

该服务只暴露结构化订单快照与聚合能力，不接收用户 ID；应用层通过 profile
上下文限制到当前本地用户，工具本身也可被离线 smoke 测试直接调用。
"""
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from app.services import order_query as service

mcp = FastMCP("shoplens-orders")


@mcp.tool()
async def search_orders(
    start_at: str,
    end_at: str,
    keyword: str | None = None,
    category: str | None = None,
    platform: str | None = None,
) -> list[dict]:
    """按时间、关键词、品类或平台检索当前本地用户的订单快照。"""
    return await service.search_orders(
        datetime.fromisoformat(start_at), datetime.fromisoformat(end_at),
        keyword=keyword, category=category, platform=platform,
    )


@mcp.tool()
async def summarize_orders(
    start_at: str,
    end_at: str,
    keyword: str | None = None,
    category: str | None = None,
    platform: str | None = None,
    group_by: str | None = None,
) -> dict:
    """返回订单数、总金额、平均客单价及可选分组。"""
    orders = await service.search_orders(
        datetime.fromisoformat(start_at), datetime.fromisoformat(end_at),
        keyword=keyword, category=category, platform=platform,
    )
    return service.summarize_orders(orders, group_by)


@mcp.tool()
def group_orders(orders: list[dict], dimension: str) -> list[dict]:
    """对结构化订单快照按品类或平台分组。"""
    if dimension not in {"category", "platform"}:
        raise ValueError("dimension 必须是 category 或 platform")
    return service.group_orders(orders, dimension)


if __name__ == "__main__":
    mcp.run(transport="stdio")
