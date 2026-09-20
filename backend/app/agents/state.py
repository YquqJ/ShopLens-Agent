"""AgentState：单 Agent StateGraph 的共享状态。

字段职责：
- messages：对话全量（add reducer 累加），澄清反问/推荐语都往里写；
- preferences：clarify 提取的结构化偏好（品类/预算/风格/材质/场景）；
- candidates → quotes → normalized → recommendation：链路产物，逐步传递；
- draft_order / order：M4 下单闭环用，M3 先占位；
- hop_count：多轮澄清次数，防死循环用。
"""
import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage


class AgentState(TypedDict):
    messages: Annotated[list[Any], operator.add]
    preferences: dict
    intent: str
    needs_action: str          # "" | "clarify" | "shopping"
    candidates: list[dict]
    quotes: list[dict]
    normalized: list[dict]
    recommendation: dict
    draft_order: dict | None
    order: dict | None
    lock_attempts: int   # 下单前二次询价「涨价」重确认次数
    price_raised: bool   # 最近一次二次询价是否涨价
    hop_count: int


def initial_state(message: str) -> AgentState:
    """构造初始状态（首轮用户消息 + 各字段默认值）。"""
    return {
        "messages": [HumanMessage(message)],
        "preferences": {},
        "intent": "",
        "needs_action": "",
        "candidates": [],
        "quotes": [],
        "normalized": [],
        "recommendation": {},
        "draft_order": None,
        "order": None,
        "lock_attempts": 0,
        "price_raised": False,
        "hop_count": 0,
    }