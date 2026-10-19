"""确认节点：interrupt 挂起，回显草稿订单等人工确认（HITL）。

设计决策：
- 用 LangGraph interrupt 做下单确认的「人在回路」，checkpointer 负责断点持久化；
- 方案 B：用户在候选(sku_id)/平台(platform)间做选择，confirm 按选择重建 draft；
  仅首轮确认应用选择；涨价重确认（lock_attempts>0）保留锁价后的 draft，不再重建；
- resume 值为 {"confirm": bool, "sku_id"?: str, "platform"?: str}，取消则 draft_order 置 None 走 END。
"""
from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from app.agents.state import AgentState


_CANCEL_ORDER_REPLY = "好的，已取消本次下单。你可以继续说明购物需求，也可以说“退出导购”结束本轮。"
_INVALID_SELECTION_REPLY = "你选择的商品不在当前推荐结果中，本次下单已停止，请重新发起导购。"


def _draft(item: dict, quote: dict) -> dict:
    """由候选 item + 指定平台 quote 组装草稿订单（数值全部取自数据）。"""
    return {
        "sku_id": item["sku_id"],
        "title": item["title"],
        "url": item.get("url", ""),
        "catalog_price": item.get("catalog_price"),
        "platform": quote["platform"],
        "price": quote["price"],
        "discount": quote["discount"],
        "shipping_fee": quote["shipping_fee"],
        "eta_days": quote["eta_days"],
        "final_price": quote["final_price"],
    }


def _build_draft(item: dict) -> dict:
    """默认草稿：取该候选的综合最优解（best）。"""
    return _draft(item, item["best"])


def _resolve_selection(normalized: list[dict], decision: dict) -> dict | None:
    """按用户的候选/平台选择重建 draft；选择不合法（sku 或 platform 不存在）返回 None。"""
    sku_id = decision.get("sku_id")
    if not sku_id:
        return None
    item = next((n for n in normalized if n["sku_id"] == sku_id), None)
    if item is None:
        return None
    platform = decision.get("platform")
    if platform:
        quote = next((q for q in item.get("quotes", []) if q["platform"] == platform), None)
        if quote is None:
            return None
        return _draft(item, quote)
    return _build_draft(item)


async def confirm_node(state: AgentState) -> dict:
    normalized = state.get("normalized", [])
    if not normalized:
        return {"draft_order": None, "order": {"status": "skipped"}}
    re_confirm = state.get("lock_attempts", 0) > 0 and bool(state.get("draft_order"))
    if re_confirm:
        draft = state["draft_order"]          # 涨价重确认：用锁价后的最新价
    else:
        draft = _build_draft(normalized[0])    # 默认：归一后最优解第一条

    decision = interrupt({"type": "confirm_order", "draft_order": draft})
    confirm = decision.get("confirm") if isinstance(decision, dict) else bool(decision)
    if not confirm:
        # 取消下单：回写可继续购物或退出导购的提示，会话历史还原时原地展示
        return {
            "draft_order": None,
            "order": {"status": "cancelled"},
            "messages": [AIMessage(_CANCEL_ORDER_REPLY)],
        }
    # 方案 B：仅首轮确认应用「候选/平台选择」，重确认保留锁定价
    if not re_confirm and isinstance(decision, dict):
        has_selection = bool(decision.get("sku_id") or decision.get("platform"))
        selected = _resolve_selection(normalized, decision) if has_selection else draft
        if selected is None:
            return {
                "draft_order": None,
                "order": {"status": "invalid_selection"},
                "messages": [AIMessage(_INVALID_SELECTION_REPLY)],
            }
        draft = selected
    return {"draft_order": draft, "order": {"status": "confirmed"}}
