"""AgentState：单 Agent StateGraph 的共享状态。

字段职责：
- messages：对话全量（add reducer 累加），澄清反问/推荐语都往里写；
- preferences：clarify 提取的结构化偏好（品类/预算/风格/材质/场景）；
- candidates → quotes → normalized → recommendation：链路产物，逐步传递；
- draft_order / order：M4 下单闭环用，M3 先占位；
- hop_count：多轮澄清次数，防死循环用。
"""
import operator
import time
from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage

_RESET_SESSION_PREFERENCES = "__reset_session_preferences__"


def merge_preferences(a: dict, b: dict) -> dict:
    """跨轮累积偏好：b 的非空字段覆盖 a（历史已确认信息不回退）。"""
    if b.get(_RESET_SESSION_PREFERENCES):
        return {
            key: value
            for key, value in b.items()
            if key != _RESET_SESSION_PREFERENCES and value not in (None, "", [])
        }
    merged = dict(a or {})
    for k, v in (b or {}).items():
        if v not in (None, "", []):
            merged[k] = v
    return merged


def is_completed_order(state: dict) -> bool:
    """只有成功下单才会在下一条聊天消息开始新的导购上下文。"""
    order = state.get("order") if isinstance(state, dict) else None
    return isinstance(order, dict) and order.get("status") == "placed"


class AgentState(TypedDict):
    messages: Annotated[list[Any], operator.add]
    preferences: Annotated[dict, merge_preferences]   # 跨轮累积（reducer 合并非空字段）
    profile_preferences: dict  # 用户授权的跨会话长期偏好（不混入本轮 preferences）
    memory_events: Annotated[list[dict], operator.add]  # trace-safe 记忆读取/写入事件
    intent: str
    needs_action: str          # "" | "clarify" | "shopping"
    clarify_question: str      # M8：澄清问句（独立序列化字段，供前端透出；messages 不含）
    reply: str                 # M8：route 非导购兜底回复（独立序列化字段）
    candidates: list[dict]
    zdm_mode: bool             # 方案 B：真实源（值海纳）模式，检索后直连推荐，跳过比价/下单闭环
    quotes: list[dict]
    normalized: list[dict]
    degraded_sources: list[str]   # M6：本次比价中熔断/失败的平台（"暂不可比"，供推荐透出）
    recommendation: dict
    draft_order: dict | None
    order: dict | None
    lock_attempts: int   # 下单前二次询价「涨价」重确认次数
    price_raised: bool   # 最近一次二次询价是否涨价
    hop_count: int


def initial_state(message: str, *, reset_preferences: bool = False) -> AgentState:
    """构造初始状态（首轮用户消息 + 各字段默认值）。"""
    return {
        "messages": [HumanMessage(message, additional_kwargs={"ts": time.time()})],
        "preferences": {_RESET_SESSION_PREFERENCES: True} if reset_preferences else {},
        "profile_preferences": {},
        "memory_events": [],
        "intent": "",
        "needs_action": "",
        "clarify_question": "",
        "reply": "",
        "candidates": [],
        "zdm_mode": False,
        "quotes": [],
        "normalized": [],
        "degraded_sources": [],
        "recommendation": {},
        "draft_order": None,
        "order": None,
        "lock_attempts": 0,
        "price_raised": False,
        "hop_count": 0,
    }
