"""值得买海纳开放平台真实商品检索服务（方案 B：可开关的真实数据源）。

职责划分（配合 mcp/zhidemai.py 的 FastMCP 薄封装）：
- search_real_products()：直连 gw-openapi.zhidemai.com/product/v1 召回真实商品，
  鉴权用 x-api-key 请求头（ZDM_API_KEY），返回规整后的商品摘要。

设计决策：
- 与仿真比价源（mcp/_sim.py）职责分离：本服务是「真实第三方数据源」，
  只做检索层真实候选召回，不进熔断/混沌/黄金集评测路径（那是仿真源的可靠性叙事）；
- 值海纳是聚合好价平台，返回的是「某商品在当前商城的报价」，无跨平台 sku 对齐，
  故不参与 services/normalize.py 的跨源归一，只作为真实数据演示源；
- 价格以 page_price（数值）为准；price_tags 保留「常卖价/历史最低」供展示；
  promotional_info 是优惠券文案，原样透传（不做 LLM 现编）。
"""
import uuid

import httpx

from app.core.config import settings

# 海纳开放平台网关 + 商品检索 Pro 端点（字段见官网控制台接口文档）
ZDM_BASE_URL = "https://gw-openapi.zhidemai.com"
PRODUCT_SEARCH_PATH = "/product/v1"

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


async def search_real_products(
    product_query: str,
    *,
    size: int = 20,
    brand_name: str | None = None,
    category_name: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    sort: str | None = None,
) -> list[dict]:
    """按商品词直连海纳开放平台检索真实商品，返回规整后的商品摘要列表。

    必填业务参数 product_query；其余为可选筛选（商城/品牌/类目/整数价格区间/排序）。
    """
    if not settings.ZDM_API_KEY:
        raise RuntimeError("ZDM_API_KEY 未配置，无法调用海纳开放平台（请在项目根 .env 填写）")

    # 组装请求：required = product_query / request_from / question_id（question_id 每次唯一）
    payload: dict = {
        "product_query": product_query,
        "request_from": "shoplens",
        "question_id": uuid.uuid4().hex[:16],
        "query_process": 1,
        "size": size,
    }
    # 可选筛选，仅填非空字段（空串/None 不送，避免网关参数校验报错）
    for key, value in (
        ("brand_name", brand_name),
        ("category_name", category_name),
        ("min_price", min_price),
        ("max_price", max_price),
        ("sort", sort),
    ):
        if value not in (None, ""):
            payload[key] = value

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{ZDM_BASE_URL}{PRODUCT_SEARCH_PATH}",
            headers={
                "Content-Type": "application/json",
                "x-api-key": settings.ZDM_API_KEY,
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("error_code") not in (0, "0", None):
        raise RuntimeError(f"海纳开放平台返回错误：{data.get('error_msg')}")

    rows = (data.get("data") or {}).get("rows") or []
    return [_summary(row) for row in rows]


def _summary(row: dict) -> dict:
    """原始行 -> 规整商品摘要（紧凑字段，供 LLM 读取与前端展示）。"""
    return {
        "title": row.get("title"),
        "price": row.get("page_price"),  # 当前价（数值）
        "price_display": row.get("price"),  # 展示价字符串，如 ¥13989
        "mall": row.get("mall_name"),  # 商城/平台名
        "brand": row.get("brand_name"),
        "category": row.get("cate3_name") or row.get("cate2_name"),
        "promotion": row.get("promotional_info"),  # 优惠券/促销文案
        "good_comments_share": row.get("good_comments_share"),
        "price_tags": row.get("price_tags"),  # [{"title":"常卖价","price":...}]
        "url": row.get("url"),
        "picture": row.get("main_picture"),
    }