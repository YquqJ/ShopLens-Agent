"""入口路由：在复杂澄清前区分购物、记忆管理和其他请求。"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from typing import Literal

from app.agents.llm import LIGHT_MODEL, get_structured_llm
from app.agents.nodes.memory import parse_memory_command
from app.agents.state import AgentState


class RouteResult(BaseModel):
    intent: Literal["shopping", "memory", "other"] = Field(
        ..., description="shopping=购物需求；memory=明确的长期偏好记忆指令；other=其他"
    )


_SYSTEM = (
    "判断用户输入意图，只输出 intent。"
    "想买东西、咨询商品选品比价，或在已有购物会话中补充预算/颜色等条件=shopping；"
    "明确要求记住、更新或忘记长期颜色/风格/材质偏好，且不是同时提出购物需求=memory；"
    "问候、闲聊、售后、无关内容=other。"
)

# 非导购兜底回复（同文本走 reply 字段透出前端，messages 不可序列化会被 SSE 排除）
_REPLY = "我是导购助手，可帮你比价选品、下单。说说你的购物需求吧～"
_MEMORY_REPLY = "已更新跨会话偏好。"

_SHOPPING_MARKERS = ("买", "推荐", "比价", "价格", "预算", "商品", "衬衫", "外套", "卫衣", "裤装", "鞋", "T恤")


def _latest_text(state: AgentState) -> str:
    messages = state.get("messages") or []
    return str(messages[-1].content) if messages else ""


def _fast_path(state: AgentState) -> str | None:
    """确定性短路：避免明确记忆指令和已有购物上下文重复调用路由模型。"""
    text = _latest_text(state).strip()
    memory_command = parse_memory_command(text)
    if memory_command and not any(marker in text for marker in _SHOPPING_MARKERS):
        return "memory"

    preferences = state.get("preferences") or {}
    if preferences.get("category") or preferences.get("budget") is not None:
        return "shopping"
    return None


async def route_node(state: AgentState) -> dict:
    fast_intent = _fast_path(state)
    if fast_intent is not None:
        return {
            "intent": fast_intent,
            "reply": _MEMORY_REPLY if fast_intent == "memory" else "",
        }

    structured = get_structured_llm(LIGHT_MODEL, RouteResult)
    latest = _latest_text(state)
    result: RouteResult = await structured.ainvoke([
        SystemMessage(_SYSTEM), HumanMessage(latest),
    ])
    update: dict = {"intent": result.intent, "reply": ""}
    if result.intent == "memory":
        update["reply"] = _MEMORY_REPLY
    elif result.intent == "other":
        update["reply"] = _REPLY
        update["messages"] = [AIMessage(_REPLY)]
    return update
