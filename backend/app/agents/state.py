"""AgentState：单 Agent StateGraph 的共享状态。

字段职责：
- messages：对话全量（add reducer 累加），澄清反问/推荐语都往里写；
- preferences：clarify 提取的结构化偏好（品类/预算/风格/材质/场景）；
- candidates → product_evidence → comparison → recommendation：真实商品事实链路产物；
- draft_order / order：确认式模拟下单闭环；
- hop_count：多轮澄清次数，防死循环用。
"""
import operator
import time
import uuid
from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage

_RESET_SESSION_PREFERENCES = "__reset_session_preferences__"
_CLEAR_PREFERENCE_FIELDS = "__clear_preference_fields__"


def merge_preferences(a: dict, b: dict) -> dict:
    """跨轮累积偏好：b 的非空字段覆盖 a（历史已确认信息不回退）。"""
    if b.get(_RESET_SESSION_PREFERENCES):
        return {
            key: value
            for key, value in b.items()
            if key not in {_RESET_SESSION_PREFERENCES, _CLEAR_PREFERENCE_FIELDS}
            and value not in (None, "", [])
        }
    merged = dict(a or {})
    for key in b.get(_CLEAR_PREFERENCE_FIELDS, []):
        merged.pop(key, None)
    for k, v in (b or {}).items():
        if k not in {_RESET_SESSION_PREFERENCES, _CLEAR_PREFERENCE_FIELDS} and v not in (None, "", []):
            merged[k] = v
    return merged


def is_completed_order(state: dict) -> bool:
    """只有成功下单才会在下一条聊天消息开始新的导购上下文。"""
    order = state.get("order") if isinstance(state, dict) else None
    return isinstance(order, dict) and order.get("status") == "placed"


def should_reset_session(state: dict) -> bool:
    """下一轮消息是否需要清空当前会话的临时导购条件。"""
    return is_completed_order(state) or bool(state.get("session_reset_pending"))


class AgentState(TypedDict):
    messages: Annotated[list[Any], operator.add]
    turn_id: str
    recommendation_history: Annotated[list[dict], operator.add]
    order_history: Annotated[list[dict], operator.add]
    order_query: dict | None
    order_results: list[dict]
    order_summary: dict | None
    order_clarification: str | None
    order_error: str | None
    preferences: Annotated[dict, merge_preferences]   # 跨轮累积（reducer 合并非空字段）
    profile_preferences: dict  # 用户授权的跨会话长期偏好（不混入本轮 preferences）
    memory_events: Annotated[list[dict], operator.add]  # trace-safe 记忆读取/写入事件
    intent: str          # "shopping" | "memory" | "other"
    needs_action: str          # "" | "clarify" | "shopping"
    clarify_question: str      # M8：澄清问句（独立序列化字段，供前端透出；messages 不含）
    trace_preferences: dict     # 当前澄清轮合并后的偏好，仅供 Trace 展示
    reply: str                 # M8：route 非导购兜底回复（独立序列化字段）
    candidates: list[dict]
    product_evidence: dict[str, list[dict]]
    comparison: dict
    order_intent_id: str | None
    zdm_mode: bool             # 值得买真实源模式
    degraded_sources: list[str]
    recommendation: dict
    recommendation_intro: str
    draft_order: dict | None
    order: dict | None
    lock_attempts: int   # 下单前二次询价「涨价」重确认次数
    price_raised: bool   # 最近一次二次询价是否涨价
    hop_count: int
    session_reset_pending: bool  # 真实源选择完成后，下一轮清空临时偏好


def initial_state(message: str, *, reset_preferences: bool = False) -> AgentState:
    """构造初始状态（首轮用户消息 + 各字段默认值）。"""
    turn_id = str(uuid.uuid4())
    return {
        "messages": [HumanMessage(message, additional_kwargs={"ts": time.time(), "turn_id": turn_id})],
        "turn_id": turn_id,
        "recommendation_history": [],
        "order_history": [],
        "order_query": None,
        "order_results": [],
        "order_summary": None,
        "order_clarification": None,
        "order_error": None,
        "preferences": {_RESET_SESSION_PREFERENCES: True} if reset_preferences else {},
        "profile_preferences": {},
        "memory_events": [],
        "intent": "",
        "needs_action": "",
        "clarify_question": "",
        "trace_preferences": {},
        "reply": "",
        "candidates": [],
        "product_evidence": {},
        "comparison": {},
        "order_intent_id": None,
        "zdm_mode": False,
        "degraded_sources": [],
        "recommendation": {},
        "recommendation_intro": "",
        "draft_order": None,
        "order": None,
        "lock_attempts": 0,
        "price_raised": False,
        "hop_count": 0,
        "session_reset_pending": False,
    }
