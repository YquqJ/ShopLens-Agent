"""下单节点：最终锁价后落库生成订单。"""
from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.services import order as order_service


async def place_order_node(state: AgentState, config: RunnableConfig) -> dict:
    draft = state.get("draft_order")
    if not draft:
        return {"order": {"status": "cancelled"}}
    thread_id = config["configurable"].get("thread_id")
    order = await order_service.place_order(draft, thread_id)
    return {"order": order}