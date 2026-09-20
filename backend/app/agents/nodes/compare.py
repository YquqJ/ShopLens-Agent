"""比价节点：对每个候选 SKU 调三平台报价 + 券 + 物流（走 MCP 工具）。

设计决策：
- 工具经 graph 调用的 config 注入（configurable.mcp_tools 为 name->tool 映射），
  由上游在事件循环内加载好、与图共享同一 MCP 会话；
- v0 不做熔断/降级：单个源故障会直接抛出并中断本节点（M6 再隔离）。
"""
import json

from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState

# 平台 -> 该平台比价工具名（adapter 用 tool_name_prefix=True，得 {server}_{tool}）
PRICE_TOOL = {"jd": "price_jd_get_quote", "taobao": "price_taobao_get_quote", "pdd": "price_pdd_get_quote"}
COUPON_TOOL = "coupon_get_coupons"
LOGISTICS_TOOL = "logistics_get_delivery"


def _unwrap(result) -> dict:
    """MCP 工具结果（[{'type':'text','text':'<json>'}] / str / dict）统一成 dict。"""
    if (
        isinstance(result, list)
        and result
        and isinstance(result[0], dict)
        and isinstance(result[0].get("text"), str)
    ):
        return json.loads(result[0]["text"])
    if isinstance(result, str):
        return json.loads(result)
    return result


async def compare_node(state: AgentState, config: RunnableConfig) -> dict:
    tools = config["configurable"]["mcp_tools"]
    quotes: list[dict] = []
    for cand in state.get("candidates", []):
        sku = cand["sku_id"]
        for platform, price_tool_name in PRICE_TOOL.items():
            p = _unwrap(await tools[price_tool_name].ainvoke({"sku_id": sku}))
            c = _unwrap(await tools[COUPON_TOOL].ainvoke({"sku_id": sku, "platform": platform}))
            lg = _unwrap(await tools[LOGISTICS_TOOL].ainvoke({"sku_id": sku, "platform": platform}))
            quotes.append({
                "sku_id": sku,
                "platform": platform,
                "price": p["price"],
                "coupon": c.get("coupon"),
                "eta_days": lg["eta_days"],
                "shipping_fee": lg["shipping_fee"],
            })
    return {"quotes": quotes}