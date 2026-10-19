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


_INVALID_SELECTION_REPLY = "你选择的商品不在当前推荐结果中，本次下单已停止，请重新发起导购。"


def _draft(item: dict, quote: dict) -> dict:
    """由候选 item + 指定平台 quote 组装草稿订单（数值全部取自数据）。"""
    return {
        "sku_id": item["sku_id"],
        "title": item["title"],
        "url": item.get("url", ""),
        "image_url": item.get("image_url", ""),
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


def _real_quote(candidate: dict) -> dict:
    price = candidate.get("page_price")
    return {
        "platform": candidate.get("mall") or "值得买",
        "price": price,
        "discount": 0,
        "shipping_fee": 0,
        "eta_days": 0,
        "final_price": price,
    }


def _real_item(candidate: dict) -> dict:
    return {
        "sku_id": candidate["sku_id"],
        "title": candidate.get("title") or "",
        "url": candidate.get("url") or "",
        "image_url": candidate.get("image_url") or candidate.get("picture") or "",
        "catalog_price": candidate.get("catalog_price") or candidate.get("page_price"),
        "best": _real_quote(candidate),
    }


def _build_real_draft(candidates: list[dict], recommendation: dict, decision: dict | None = None) -> dict | None:
    items = {candidate.get("sku_id"): _real_item(candidate) for candidate in candidates}
    recommended = recommendation.get("items") if isinstance(recommendation, dict) else []
    allowed = {item.get("sku_id") for item in recommended if isinstance(item, dict)}
    sku_id = None
    if isinstance(decision, dict):
        sku_id = decision.get("sku_id")
    if not sku_id and isinstance(recommendation, dict):
        sku_id = recommendation.get("best_sku_id")
    if not sku_id and recommended:
        sku_id = recommended[0].get("sku_id")
    if sku_id not in allowed or sku_id not in items:
        return None
    return _draft(items[sku_id], items[sku_id]["best"])


async def confirm_node(state: AgentState) -> dict:
    normalized = state.get("normalized", [])
    candidates = state.get("candidates", [])
    recommendation = state.get("recommendation") or {}
    real_mode = bool(candidates and not normalized and recommendation.get("items"))
    if not normalized and not real_mode:
        return {"draft_order": None, "order": {"status": "skipped"}}
    re_confirm = state.get("lock_attempts", 0) > 0 and bool(state.get("draft_order"))
    if re_confirm:
        draft = state["draft_order"]          # 涨价重确认：用锁价后的最新价
    else:
        draft = (_build_real_draft(candidates, recommendation)
                 if real_mode else _build_draft(normalized[0]))

    if draft is None:
        return {"draft_order": None, "order": {"status": "invalid_selection"}}

    decision = interrupt({"type": "confirm_order", "draft_order": draft})
    confirm = decision.get("confirm") if isinstance(decision, dict) else bool(decision)
    if not confirm:
        return {
            "draft_order": None,
            "order": {"status": "cancelled"},
            "order_history": [{
                "turn_id": state.get("turn_id"),
                "order": {"status": "cancelled"},
            }],
        }
    # 方案 B：仅首轮确认应用「候选/平台选择」，重确认保留锁定价
    if not re_confirm and isinstance(decision, dict):
        has_selection = bool(decision.get("sku_id") or decision.get("platform"))
        selected = (
            _build_real_draft(candidates, recommendation, decision)
            if real_mode and has_selection
            else _resolve_selection(normalized, decision) if has_selection else draft
        )
        if selected is None:
            return {
                "draft_order": None,
                "order": {"status": "invalid_selection"},
                "messages": [AIMessage(_INVALID_SELECTION_REPLY)],
            }
        draft = selected
    return {"draft_order": draft, "order": {"status": "confirmed"}}
