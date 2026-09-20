"""澄清节点：LLM 从最新用户消息提取结构化偏好，关键信息缺失则反问。

设计决策：
- 关键信息 = 品类 + 预算，两者缺一即 needs_clarification，追问收敛需求；
- 偏好用结构化输出（Pydantic）而非 JSON 字符串解析，避免格式抖动。
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.agents.state import AgentState


class Preferences(BaseModel):
    category: str | None = Field(None, description="品类，如 项链/手链/戒指")
    budget: float | None = Field(None, description="预算（元）")
    style: str | None = Field(None, description="风格偏好，如 简约/复古")
    material: str | None = Field(None, description="材质，如 18K金/925银")
    scene: str | None = Field(None, description="使用/赠送场景，如 送女友")


class ClarifyResult(BaseModel):
    needs_clarification: bool = Field(..., description="是否缺少关键信息需追问")
    preferences: Preferences
    clarify_question: str = Field("", description="追问句（needs_clarification=True 时填）")


_SYSTEM = "你是导购澄清助手。从用户一句话里提取购物偏好（品类/预算/风格/材质/场景），拿不准的字段留空。关键信息=品类+预算，缺任一即 needs_clarification=True。"


def _default_question(p: Preferences) -> str:
    """LLM 未填问句时，按缺失的关键字段（品类/预算）确定性拼默认追问，避免空问句。"""
    missing = []
    if not p.category:
        missing.append("想买什么品类（如项链、手链、戒指）")
    if p.budget is None:
        missing.append("预算大概多少")
    if missing:
        return "为了更精准地推荐，" + "、".join(missing) + "？"
    return "能否再补充一下你的偏好？"


async def clarify_node(state: AgentState) -> dict:
    structured = get_llm().with_structured_output(ClarifyResult)
    latest = state["messages"][-1].content if state["messages"] else ""
    result: ClarifyResult = await structured.ainvoke([
        SystemMessage(_SYSTEM), HumanMessage(latest),
    ])
    update: dict = {
        "preferences": result.preferences.model_dump(),
        "hop_count": state.get("hop_count", 0) + 1,
    }
    if result.needs_clarification:
        update["needs_action"] = "clarify"
        # 文本走独立字段透出给前端（messages 里是 AIMessage，SSE 会排除不可序列化对象）；
        # 实测 LLM 可能返回 needs_clarification=True 却留空问句，故空则回退确定性默认问句
        question = result.clarify_question or _default_question(result.preferences)
        update["clarify_question"] = question
        update["messages"] = [AIMessage(question)]
    else:
        update["needs_action"] = "shopping"
        update["clarify_question"] = ""
    return update