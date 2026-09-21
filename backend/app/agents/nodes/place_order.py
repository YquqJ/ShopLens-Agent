"""下单节点：最终锁价后落库生成订单，并把「已下单」摘要写进对话消息（供会话历史还原）。"""
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.services import order as order_service


def _fmt_price(v) -> str:
    """金额格式：整元不带小数，否则保留两位（与前端下线摘要文案保持一致）。"""
    if v is None:
        return ""
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:.2f}"


async def place_order_node(state: AgentState, config: RunnableConfig) -> dict:
    draft = state.get("draft_order")
    if not draft:
        return {"order": {"status": "cancelled"}}
    thread_id = config["configurable"].get("thread_id")
    order = await order_service.place_order(draft, thread_id)
    # 下单成功摘要回写 messages：checkpointer 累加保存，切会话回来据此在原位还原「已下单」
    summary = f"已下单「{draft['title']}」· ¥{_fmt_price(draft['final_price'])}"
    return {"order": order, "messages": [AIMessage(summary)]}