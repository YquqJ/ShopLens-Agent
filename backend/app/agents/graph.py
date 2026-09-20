"""图组装：clarify → route → retrieve → compare → normalize → recommend。

设计决策：
- 单 Agent StateGraph（非多 Agent），需要 interrupt/checkpointer 是选 LangGraph 的理由；
- clarify 缺关键信息 → END（反问）；route 非导购 → END（兜底回复）；
- 工具不绑定在 LLM 上，compare 节点按 configurable.mcp_tools 直调（编排而非自由 tool-call）。
"""
from langgraph.graph import END, START, StateGraph

from app.agents.nodes import clarify, compare, normalize, recommend, retrieve, route
from app.agents.state import AgentState


def _after_clarify(state: AgentState) -> str:
    return "route" if state.get("needs_action") == "shopping" else "end"


def _after_route(state: AgentState) -> str:
    return "retrieve" if state.get("intent") == "shopping" else "end"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("clarify", clarify.clarify_node)
    g.add_node("route", route.route_node)
    g.add_node("retrieve", retrieve.retrieve_node)
    g.add_node("compare", compare.compare_node)
    g.add_node("normalize", normalize.normalize_node)
    g.add_node("recommend", recommend.recommend_node)

    g.add_edge(START, "clarify")
    g.add_conditional_edges("clarify", _after_clarify, {"route": "route", "end": END})
    g.add_conditional_edges("route", _after_route, {"retrieve": "retrieve", "end": END})
    g.add_edge("retrieve", "compare")
    g.add_edge("compare", "normalize")
    g.add_edge("normalize", "recommend")
    g.add_edge("recommend", END)
    return g.compile()