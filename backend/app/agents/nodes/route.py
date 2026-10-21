"""入口路由：在复杂澄清前区分购物、订单、退出导购和其他请求。"""
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from typing import Literal

from app.agents.llm import LIGHT_MODEL, get_structured_llm
from app.agents.state import AgentState


class RouteResult(BaseModel):
    intent: Literal["shopping", "order_search", "order_summary", "cancel", "other"] = Field(
        ..., description="shopping=购物需求；order_search=订单明细；order_summary=消费统计；cancel=明确退出本轮导购；other=其他"
    )


_SYSTEM = (
    "判断用户输入意图，只输出 intent。"
    "想买东西、咨询商品选品比价，或在已有购物会话中补充预算/颜色等条件=shopping；"
    "查询历史订单明细（买了什么、买过哪些商品）=order_search；查询消费金额、订单数、平均客单价或分组统计=order_summary；"
    "明确表示不要了、不买了、退出本轮导购=cancel；"
    "问候、闲聊、售后、无关内容=other。"
)

# 非导购兜底回复（同文本走 reply 字段透出前端，messages 不可序列化会被 SSE 排除）
_REPLY = "我是导购助手，可帮你比价选品、下单。说说你的购物需求吧～"
_CANCEL_REPLY = "好的，本轮导购已结束。之后有新的购物需求时，直接告诉我即可。"

_SHOPPING_MARKERS = ("买", "推荐", "比价", "价格", "预算", "商品", "衬衫", "外套", "卫衣", "裤装", "鞋", "T恤")
_ORDER_MARKERS = ("昨天", "前天", "上周", "上个月", "最近", "今年", "买了什么", "买了哪些", "买过哪些", "花了多少钱", "买了几单", "平均每单", "订单", "消费")

_CANCEL_PATTERNS = (
    re.compile(r"^(?:我)?(?:不要了|不买了|先不买了|暂时不买了|不用了|不需要了|不考虑了)[，。！？!?,\s]*$"),
    re.compile(r"^(?:退出|结束|取消)(?:本轮|这轮|当前|这次)?(?:导购|购物|推荐|购买)(?:流程|会话)?[，。！？!?,\s]*$"),
)
_CANCEL_HINTS = ("算了", "不想", "不买", "不要", "暂时", "停止", "退出", "取消", "不需要", "不用", "不考虑", "先看看")


def _latest_text(state: AgentState) -> str:
    messages = state.get("messages") or []
    return str(messages[-1].content) if messages else ""


def _is_cancel_command(text: str) -> bool:
    """只匹配完整退出短语，避免把「推荐商品」或「不要白色」误判为取消。"""
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    return any(pattern.search(normalized) for pattern in _CANCEL_PATTERNS)


def _cancel_update() -> dict:
    """退出导购：清空当前会话短期偏好，但保留长期画像。"""
    return {
        "intent": "cancel",
        "reply": _CANCEL_REPLY,
        "messages": [AIMessage(_CANCEL_REPLY)],
        "preferences": {"__reset_session_preferences__": True},
    }


def _fast_path(state: AgentState) -> str | None:
    """确定性短路：避免明确记忆指令和已有购物上下文重复调用路由模型。"""
    text = _latest_text(state).strip()
    if _is_cancel_command(text):
        return "cancel"
    temporal = any(marker in text for marker in ("今天", "昨天", "前天", "上周", "上个月", "最近", "今年"))
    if (any(marker in text for marker in _ORDER_MARKERS) and any(
        marker in text for marker in ("买了什么", "买了哪些", "买过", "花了", "几单", "平均", "订单", "消费")
    )) or (temporal and "买" in text and "想买" not in text and "推荐" not in text):
        return "order_summary" if any(marker in text for marker in ("花了", "几单", "平均", "统计", "消费")) else "order_search"
    # 已有购物上下文时，模糊退出表达交给轻量模型，不被 shopping fast path 截断。
    if any(marker in text for marker in _CANCEL_HINTS):
        return None
    preferences = state.get("preferences") or {}
    if preferences.get("category") or preferences.get("budget") is not None:
        return "shopping"
    return None


async def route_node(state: AgentState) -> dict:
    fast_intent = _fast_path(state)
    if fast_intent is not None:
        if fast_intent == "cancel":
            return _cancel_update()
        return {"intent": fast_intent, "reply": ""}

    structured = get_structured_llm(LIGHT_MODEL, RouteResult)
    latest = _latest_text(state)
    result: RouteResult = await structured.ainvoke([
        SystemMessage(_SYSTEM), HumanMessage(latest),
    ])
    update: dict = {"intent": result.intent, "reply": ""}
    if result.intent == "cancel":
        update = _cancel_update()
    elif result.intent == "other":
        update["reply"] = _REPLY
        update["messages"] = [AIMessage(_REPLY)]
    return update
