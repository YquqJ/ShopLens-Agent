"""推荐节点：LLM 基于归一结果生成可溯源推荐。

设计决策：
- 数值（sku_id/title/final_price/platform/attributes）一律从数据（normalized）取，
  只让 LLM 生成本文「理由/总结」，杜绝价格被 LLM 现编；
- 有预算时先做非 LLM 过滤（best 到手价 ≤ 预算），选不出再退回全部，保证 ≥1 条。
"""
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.llm import get_llm
from app.agents.state import AgentState

_TOP_N = 3


class Reason(BaseModel):
    sku_id: str
    reason: str


class RecommendResult(BaseModel):
    summary: str
    reasons: list[Reason]


_SYSTEM = "你是电商导购。基于给定候选商品数据，为每条写一句可溯源推荐理由（引用商品属性），并给一句总结。理由只描述、不编造价格数值。"


async def recommend_node(state: AgentState) -> dict:
    normalized = state.get("normalized", [])
    # 预算过滤（非 LLM）：只保留 best 到手价在预算内的
    budget = (state.get("preferences") or {}).get("budget")
    pool = normalized
    if budget:
        within = [i for i in normalized if i["best"]["final_price"] <= budget]
        if within:
            pool = within
    top = pool[:_TOP_N]

    ctx_lines = []
    for item in top:
        b = item["best"]
        ctx_lines.append(
            f"- {item['sku_id']} {item['title']} 属性={item['attributes']} "
            f"最优平台={b['platform']} 到手价={b['final_price']} 快递{b['eta_days']}天"
        )
    user = (
        f"用户偏好={state.get('preferences')}\n候选：\n" + "\n".join(ctx_lines)
    )
    structured = get_llm().with_structured_output(RecommendResult)
    res: RecommendResult = await structured.ainvoke([
        SystemMessage(_SYSTEM), HumanMessage(user),
    ])

    by_sku = {item["sku_id"]: item for item in top}
    items = []
    for r in res.reasons:
        item = by_sku.get(r.sku_id)
        if item is None:
            continue
        b = item["best"]
        items.append({
            "sku_id": item["sku_id"],
            "title": item["title"],
            "final_price": b["final_price"],       # 数值取数据
            "platform": b["platform"],             # 数值取数据
            "attributes": item["attributes"],      # 数值取数据
            "reason": r.reason,                     # 文本取 LLM（挂属性，可溯源）
        })
    return {"recommendation": {"summary": res.summary, "items": items}}