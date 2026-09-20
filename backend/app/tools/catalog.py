"""应用内工具：search_products / check_stock_price 的 LangChain @tool 薄封装。

职责：只做编排与上下文提取，业务逻辑在 services/catalog.py；
工具参数不含 user_id/db（走全局依赖注入），返回值 JSON 字符串供 LLM 读取。
"""
import json

from langchain_core.tools import tool

from app.services import catalog as catalog_service


@tool
async def search_products(query: str, top_k: int = 5) -> str:
    """按自然语言检索相关商品，返回候选 SKU（含价格/库存/属性/图片/相关度）。

    Args:
        query: 用户需求描述，如"简约项链"。
        top_k: 返回候选条数，默认 5。
    """
    results = await catalog_service.search(query, top_k=top_k)
    return json.dumps(results, ensure_ascii=False)


@tool
async def check_stock_price(sku_id: str) -> str:
    """按 SKU 编号查询实时价格与库存（数据来自 Postgres，非模型编造）。

    Args:
        sku_id: 商品 SKU 编号，如 SKU0001。
    """
    info = await catalog_service.check_stock_price(sku_id)
    return json.dumps(info, ensure_ascii=False)