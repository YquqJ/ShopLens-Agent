"""确认节点：interrupt 挂起，回显草稿订单等人工确认（HITL）。

设计决策：
- 用 LangGraph interrupt 做下单确认的「人在回路」，checkpointer 负责断点持久化；
- 涨价重确认时（lock_attempts>0）复用锁价节点更新过的 draft，而非重取原始报价；
- resume 值为 {"confirm": bool}，取消则 draft_order 置 None 走 END。
"""
from langgraph.types import interrupt

from app.agents.state import AgentState


def _build_draft(item: dict) -> dict:
    b = item["best"]
    return {
        "sku_id": item["sku_id"],
        "title": item["title"],
        "platform": b["platform"],
        "price": b["price"],
        "discount": b["discount"],
        "shipping_fee": b["shipping_fee"],
        "eta_days": b["eta_days"],
        "final_price": b["final_price"],
    }


async def confirm_node(state: AgentState) -> dict:
    normalized = state.get("normalized", [])
    if not normalized:
        return {"draft_order": None, "order": {"status": "skipped"}}
    if state.get("lock_attempts", 0) > 0 and state.get("draft_order"):
        draft = state["draft_order"]          # 涨价后重确认，用已更新价
    else:
        draft = _build_draft(normalized[0])    # v0：选归一后最优解第一条

    decision = interrupt({"type": "confirm_order", "draft_order": draft})
    confirm = decision.get("confirm") if isinstance(decision, dict) else bool(decision)
    if not confirm:
        return {"draft_order": None, "order": {"status": "cancelled"}}
    return {"draft_order": draft, "order": {"status": "confirmed"}}