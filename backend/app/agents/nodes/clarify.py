"""澄清节点：LLM 从最新用户消息提取结构化偏好，关键信息缺失则反问。

设计决策：
- 关键信息 = 品类 + 预算，二者缺一即 needs_clarification，追问收敛需求；
- 偏好用结构化输出（Pydantic）而非 JSON 字符串解析，避免格式抖动；
- 多轮累积：preferences 带 merge reducer（state.py）跨轮保留；
  clarify 把历史偏好喂回 prompt，并按「合并后」结果判断缺哪些字段，
  不再只信 LLM 对最新消息的 needs_clarification（它会因读不到历史而误追问）。
"""
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.agents.state import AgentState, merge_preferences


class Preferences(BaseModel):
    category: str | None = Field(None, description="商品品类（自由填写，不限首饰），如 手机/耳机/项链/电脑")
    budget: float | None = Field(None, description="预算（元）")
    style: str | None = Field(None, description="风格偏好，如 简约/复古（无则留空）")
    material: str | None = Field(None, description="材质，如 18K金/925银（无则留空）")
    scene: str | None = Field(None, description="使用/赠送场景，如 送女友")


class ClarifyResult(BaseModel):
    needs_clarification: bool = Field(..., description="是否缺少关键信息需追问")
    preferences: Preferences
    clarify_question: str = Field("", description="追问句（needs_clarification=True 时填）")


_SYSTEM = (
    "你是导购澄清助手。从用户一句话里提取购物偏好（品类/预算/风格/材质/场景），拿不准的字段留空。"
    "品类不限首饰，手机/耳机/电脑/服饰等均可。关键信息=品类+预算，缺任一即 needs_clarification=True。"
)


def _missing(prefs: dict) -> list[str]:
    """合并后仍缺的关键字段（品类/预算）追问文案。"""
    missing = []
    if not prefs.get("category"):
        missing.append("想买什么品类（如手机、耳机、项链等）")
    if prefs.get("budget") is None:
        missing.append("预算大概多少")
    return missing


def _default_question(prefs: dict) -> str:
    """LLM 未填问句时，按合并后缺失的关键字段确定性拼默认追问。"""
    missing = _missing(prefs)
    if missing:
        return "为了更精准地推荐，" + "、".join(missing) + "？"
    return "能否再补充一下你的偏好？"


async def clarify_node(state: AgentState) -> dict:
    structured = get_llm().with_structured_output(ClarifyResult)
    latest = state["messages"][-1].content if state["messages"] else ""
    prev = state.get("preferences") or {}

    # 把历史偏好喂回 prompt：让 LLM 在已确认信息上补缺失字段，避免重复追问
    system = _SYSTEM
    if prev:
        system += (
            "\n历史已确认偏好：" + json.dumps(prev, ensure_ascii=False)
            + "。本轮在其基础上补充或纠正，未提及字段保持原值。"
        )
    result: ClarifyResult = await structured.ainvoke([
        SystemMessage(system), HumanMessage(latest),
    ])

    # 本轮提取 + 历史合并（非空覆盖，空字段回退历史）
    new_prefs = result.preferences.model_dump()
    merged = merge_preferences(prev, new_prefs)

    # 关键信息按「合并后」判断，而非只信 LLM 对最新消息的 needs_clarification
    needs = not merged.get("category") or merged.get("budget") is None

    update: dict = {
        "preferences": new_prefs,   # 经 merge reducer 与历史合并
        "hop_count": state.get("hop_count", 0) + 1,
    }
    if needs:
        update["needs_action"] = "clarify"
        question = result.clarify_question or _default_question(merged)
        update["clarify_question"] = question
        update["messages"] = [AIMessage(question)]
    else:
        update["needs_action"] = "shopping"
        update["clarify_question"] = ""
    return update