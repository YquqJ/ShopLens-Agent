"""检索节点：按偏好拼查询串，调 search_products 召回候选商品。"""
from app.agents.state import AgentState
from app.services import catalog

_TOP_K = 5


def _build_query(prefs: dict) -> str:
    """风格/材质/品类拼成查询串（缺省跳过）。"""
    parts = [str(prefs[k]) for k in ("style", "material", "category") if prefs.get(k)]
    return " ".join(parts) if parts else str(prefs.get("category", ""))


async def retrieve_node(state: AgentState) -> dict:
    prefs = state.get("preferences") or {}
    candidates = await catalog.search(_build_query(prefs), top_k=_TOP_K)
    return {"candidates": candidates}