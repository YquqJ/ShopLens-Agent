"""本地订单查询 FastMCP stdio 服务。

该服务只暴露结构化订单快照与聚合能力；每次调用都必须由应用传入当前画像标识，
避免缺少调用上下文时扩大查询范围。
"""
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from app.core.loop import install_selector_event_loop_policy
from app.services import order_query as service

mcp = FastMCP("shoplens-orders")


@mcp.tool()
async def search_orders(
    start_at: str,
    end_at: str,
    profile_key: str,
    keyword: str | None = None,
    category: str | None = None,
    platform: str | None = None,
) -> list[dict]:
    """按时间、关键词、品类或平台检索当前本地用户的订单快照。"""
    return await service.search_orders(
        datetime.fromisoformat(start_at), datetime.fromisoformat(end_at),
        keyword=keyword, category=category, platform=platform, profile_key=profile_key,
    )


@mcp.tool()
async def summarize_orders(
    start_at: str,
    end_at: str,
    profile_key: str,
    keyword: str | None = None,
    category: str | None = None,
    platform: str | None = None,
    group_by: str | None = None,
) -> dict:
    """返回订单数、总金额、平均客单价及可选分组。"""
    orders = await service.search_orders(
        datetime.fromisoformat(start_at), datetime.fromisoformat(end_at),
        keyword=keyword, category=category, platform=platform, profile_key=profile_key,
    )
    return service.summarize_orders(orders, group_by)


@mcp.tool()
def group_orders(orders: list[dict], dimension: str) -> list[dict]:
    """对结构化订单快照按品类或平台分组。"""
    if dimension not in {"category", "platform"}:
        raise ValueError("dimension 必须是 category 或 platform")
    return service.group_orders(orders, dimension)


def main() -> None:
    install_selector_event_loop_policy()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
