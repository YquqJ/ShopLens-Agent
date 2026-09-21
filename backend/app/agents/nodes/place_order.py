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


_PLATFORM_NAMES = {"jd": "京东", "taobao": "淘宝", "pdd": "拼多多", "master": "主库兜底"}


async def place_order_node(state: AgentState, config: RunnableConfig) -> dict:
    draft = state.get("draft_order")
    if not draft:
        return {"order": {"status": "cancelled"}}
    thread_id = config["configurable"].get("thread_id")
    # 是否购买首推（👍推荐品）：决定是否计入「推荐成交」漏斗段
    rec = state.get("recommendation") or {}
    top_sku = (rec.get("items") or [{}])[0].get("sku_id")
    is_recommended = bool(top_sku and draft.get("sku_id") == top_sku)
    order = await order_service.place_order(draft, thread_id, is_recommended=is_recommended)
    # 下单成功摘要回写 messages：checkpointer 累加保存，切会话回来据此在原位还原「已下单」
    platform = draft.get("platform") or ""
    summary = f"已下单「{draft['title']}」· ¥{_fmt_price(draft['final_price'])} · {_PLATFORM_NAMES.get(platform, platform)}"
    return {"order": order, "messages": [AIMessage(summary)]}