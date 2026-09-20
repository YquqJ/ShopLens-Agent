"""路由节点：判断用户意图（导购 vs 其他）。"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.agents.state import AgentState


class RouteResult(BaseModel):
    intent: str = Field(..., description="'shopping' 导购 或 'other' 其他")


_SYSTEM = "判断用户输入意图：想买东西/咨询商品选品比价=shopping；问候、闲聊、售后、无关内容=other。只输出 intent。"


async def route_node(state: AgentState) -> dict:
    structured = get_llm().with_structured_output(RouteResult)
    latest = state["messages"][-1].content if state["messages"] else ""
    result: RouteResult = await structured.ainvoke([
        SystemMessage(_SYSTEM), HumanMessage(latest),
    ])
    update: dict = {"intent": result.intent}
    if result.intent != "shopping":
        update["messages"] = [AIMessage("我是导购助手，可帮你比价选品、下单。说说你的购物需求吧～")]
    return update