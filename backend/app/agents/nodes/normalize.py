"""归一节点：调 services.normalize 做 SKU 分组聚合 + 最优解（编排层）。"""
from app.agents.state import AgentState
from app.services import normalize as normalize_service


async def normalize_node(state: AgentState) -> dict:
    profile_preferences = state.get("profile_preferences") or {}
    normalized = normalize_service.normalize(
        state.get("quotes", []),
        state.get("candidates", []),
        preferred_platforms=profile_preferences.get("preferred_platforms"),
    )
    return {"normalized": normalized}
