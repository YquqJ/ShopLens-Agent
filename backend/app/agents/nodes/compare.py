"""比价节点：对每个候选 SKU 调三平台报价 + 券 + 物流（走 MCP 工具）。

设计决策：
- 工具经 graph 调用的 config 注入（configurable.mcp_tools 为 name->tool 映射）；
- M6 起工具已套熔断/重试（infra/protect），单源失败抛 SourceError → 该平台降级「暂不可比」，
  其余源继续出结果；全部源失败 → 回退主库价（infra/degrade.fallback_to_master）；
- compare 只做编排，不再因单源故障整体中断。
"""
import json

from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.infra.degrade import fallback_to_master
from app.infra.protect import SourceError

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
    degraded: set[str] = set()
    for cand in state.get("candidates", []):
        sku = cand["sku_id"]
        base_price = cand.get("price")
        for platform, price_tool_name in PRICE_TOOL.items():
            try:
                p = _unwrap(await tools[price_tool_name].ainvoke({"sku_id": sku, "base_price": base_price}))
                c = _unwrap(await tools[COUPON_TOOL].ainvoke({"sku_id": sku, "platform": platform, "base_price": base_price}))
                lg = _unwrap(await tools[LOGISTICS_TOOL].ainvoke({"sku_id": sku, "platform": platform}))
            except SourceError:
                degraded.add(platform)  # 该平台暂不可比，跳过并继续其余源
                continue
            quotes.append({
                "sku_id": sku,
                "platform": platform,
                "price": p["price"],
                "coupon": c.get("coupon"),
                "eta_days": lg["eta_days"],
                "shipping_fee": lg["shipping_fee"],
            })
    # 全源挂：退主库价（见 infra/degrade.py）
    if not quotes:
        quotes = await fallback_to_master(state.get("candidates", []))
    return {"quotes": quotes, "degraded_sources": sorted(degraded)}
