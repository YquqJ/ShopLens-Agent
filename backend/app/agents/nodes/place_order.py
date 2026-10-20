"""下单节点：按确认时商品快照落库，并追加可恢复的订单卡片记录。"""
from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.services import order as order_service


async def place_order_node(state: AgentState, config: RunnableConfig) -> dict:
    draft = state.get("draft_order")
    if not draft:
        return {"order": {"status": "cancelled"}}
    thread_id = config["configurable"].get("thread_id")
    profile_key = config["configurable"].get("profile_id")
    # 是否购买首推（👍推荐品）：决定是否计入「推荐成交」漏斗段
    rec = state.get("recommendation") or {}
    top_sku = (rec.get("items") or [{}])[0].get("sku_id")
    is_recommended = bool(top_sku and draft.get("sku_id") == top_sku)
    order = await order_service.place_order(
        draft,
        thread_id,
        profile_key,
        is_recommended=is_recommended,
        order_intent_id=state.get("order_intent_id"),
    )
    order_id = order.get("order_id") if isinstance(order, dict) else None
    already_recorded = any(
        entry.get("turn_id") == state.get("turn_id")
        or (order_id is not None and (entry.get("order") or {}).get("order_id") == order_id)
        for entry in state.get("order_history", [])
    )
    return {
        "order": order,
        "order_history": [] if already_recorded else [{"turn_id": state.get("turn_id"), "order": order}],
    }
