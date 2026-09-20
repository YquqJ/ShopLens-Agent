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
    hop_count: int