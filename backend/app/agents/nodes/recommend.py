"""推荐节点：LLM 基于归一结果生成可溯源推荐。

设计决策：
- 数值（sku_id/title/final_price/platform/attributes）一律从数据（normalized）取，
  只让 LLM 生成本文「理由/总结」，杜绝价格被 LLM 现编；
- 有预算时先做非 LLM 过滤（best 到手价 ≤ 预算），选不出再退回全部，保证 ≥1 条；
- M7 结构化裁剪：喂给 LLM 的上下文从「字典 repr 全文」改为紧凑 key=value 摘要
  （build_user(compact=True)），不喂 normalize 前的原始报价原文。
"""
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.llm import get_llm
from app.agents.state import AgentState

_TOP_N = 3


class Reason(BaseModel):
    sku_id: str
    reason: str


class RecommendResult(BaseModel):
    summary: str
    reasons: list[Reason]


_SYSTEM = "你是电商导购。基于给定候选商品数据，为每条写一句可溯源推荐理由（引用商品属性），并给一句总结。理由只描述、不编造价格数值。"


def select_top(normalized: list[dict], budget: float | None) -> list[dict]:
    """非 LLM 预算过滤：best 到手价 ≤ 预算；无预算或过滤后为空则退回全部，取前 _TOP_N。"""
    pool = normalized
    if budget:
        within = [i for i in normalized if i["best"]["final_price"] <= budget]
        if within:
            pool = within
    return pool[:_TOP_N]


def _fmt_attrs(attrs: dict) -> str:
    """属性字典 → 紧凑「值/值」（裁剪用，省去键名与引号）。"""
    return "/".join(str(v) for v in (attrs or {}).values())


def _fmt_prefs(prefs: dict) -> str:
    """偏好字典 → 紧凑「键=值」，跳过空字段（裁剪用）。"""
    mapping = [("category", "品类"), ("budget", "预算"), ("style", "风格"),
               ("material", "材质"), ("scene", "场景")]
    parts = [f"{cn}={prefs[k]}" for k, cn in mapping if prefs.get(k) not in (None, "", [])]
    return " ".join(parts) if parts else str(prefs)


def build_user(prefs: dict, top: list[dict], degraded: list[str], *, compact: bool = True) -> str:
    """拼推荐上下文人消息：compact=True 走裁剪（M7），False 走 v0 全文（对照）。"""
    lines = []
    for item in top:
        b = item["best"]
        if compact:
            lines.append(
                f"- {item['sku_id']} {item['title']} {_fmt_attrs(item['attributes'])}"
                f" | {b['platform']} {b['final_price']}元 {b['eta_days']}天"
            )
        else:
            lines.append(
                f"- {item['sku_id']} {item['title']} 属性={item['attributes']} "
                f"最优平台={b['platform']} 到手价={b['final_price']} 快递{b['eta_days']}天"
            )
    head = f"偏好 {_fmt_prefs(prefs)}" if compact else f"用户偏好={prefs}"
    user = f"{head}\n候选：\n" + "\n".join(lines)
    if degraded:
        user += "\n注：以下平台本次暂不可比（已降级，未参与最优解）：" + "、".join(degraded)
    return user


async def recommend_node(state: AgentState) -> dict:
    normalized = state.get("normalized", [])
    prefs = state.get("preferences") or {}
    top = select_top(normalized, prefs.get("budget"))
    user = build_user(prefs, top, state.get("degraded_sources", []), compact=True)

    structured = get_llm().with_structured_output(RecommendResult)
    res: RecommendResult = await structured.ainvoke([
        SystemMessage(_SYSTEM), HumanMessage(user),
    ])

    by_sku = {item["sku_id"]: item for item in top}
    items = []
    for r in res.reasons:
        item = by_sku.get(r.sku_id)
        if item is None:
            continue
        b = item["best"]
        items.append({
            "sku_id": item["sku_id"],
            "title": item["title"],
            "final_price": b["final_price"],       # 数值取数据
            "platform": b["platform"],             # 数值取数据
            "attributes": item["attributes"],      # 数值取数据
            "reason": r.reason,                     # 文本取 LLM（挂属性，可溯源）
        })
    return {"recommendation": {"summary": res.summary, "items": items, "degraded_sources": state.get("degraded_sources", [])}}