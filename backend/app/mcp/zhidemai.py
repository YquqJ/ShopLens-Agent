"""值得买海纳开放平台真实数据源（FastMCP）：按商品词检索真实商品。

与仿真比价源（jd/taobao/pdd）定位不同：本源为「真实第三方开放平台」，
只做检索层真实候选召回，不参与跨源归一与熔断/混沌评测。
返回 JSON 字符串供 LLM 读取（业务逻辑在 services/zhidemai.py）。
"""
import json

from mcp.server.fastmcp import FastMCP

from app.services import zhidemai as zhidemai_service

mcp = FastMCP("zhidemai")


@mcp.tool()
async def search_real_products(
    product_query: str,
    size: int = 20,
    brand_name: str | None = None,
    category_name: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    sort: str | None = None,
) -> str:
    """按商品词检索真实商品（来自值得买海纳开放平台），返回价格/商城/品牌/优惠/图片/链接。

    Args:
        product_query: 商品搜索词，如 "iphone16"、"简约项链"。
        size: 返回商品数量，默认 20。
        brand_name: 可选，品牌筛选。
        category_name: 可选，品类筛选。
        min_price: 可选，最低价（仅整数）。
        max_price: 可选，最高价（必须大于最低价）。
        sort: 可选，排序方式：sale_qty / good_comments_share / price_asc / price_desc。
    """
    items = await zhidemai_service.search_real_products(
        product_query,
        size=size,
        brand_name=brand_name,
        category_name=category_name,
        min_price=min_price,
        max_price=max_price,
        sort=sort,
    )
    return json.dumps(items, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()