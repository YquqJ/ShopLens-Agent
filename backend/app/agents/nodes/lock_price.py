"""锁价节点：下单前二次询价，行情上涨则回 confirm 重确认。

设计决策：
- 下单前对同一平台重新报价，杜绝「订单价 ≠ 展示价」；
- 二次询价只重报平台价，券/物流沿用草稿（v0），新到手价 = 新价 − 券 + 运费；
- 涨价则置 price_raised 并累加 lock_attempts，图据此回 confirm。
"""
import json

from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState

_SERVER = {"jd": "price_jd", "taobao": "price_taobao", "pdd": "price_pdd"}


def _unwrap(result) -> dict:
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


async def lock_price_node(state: AgentState, config: RunnableConfig) -> dict:
    draft = state["draft_order"]
    tools = config["configurable"]["mcp_tools"]
    price_tool = tools[f"{_SERVER[draft['platform']]}_get_quote"]

    request = {"sku_id": draft["sku_id"]}
    if draft.get("catalog_price") is not None:
        request["base_price"] = draft["catalog_price"]
    p = _unwrap(await price_tool.ainvoke(request))
    new_price = p["price"]
    new_final = round(new_price - draft.get("discount", 0) + draft.get("shipping_fee", 0), 2)
    raised = new_final > draft["final_price"]

    updated = {
        **draft,
        "previous_final_price": draft["final_price"],
        "price": new_price,
        "final_price": new_final,
    }
    return {
        "draft_order": updated,
        "price_raised": raised,
        "lock_attempts": state.get("lock_attempts", 0) + (1 if raised else 0),
    }
