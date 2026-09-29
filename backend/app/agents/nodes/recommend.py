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

from app.agents.llm import MAIN_MODEL, get_structured_llm
from app.agents.state import AgentState
from app.agents.nodes.memory import build_effective_preferences

_TOP_N = 3


class Reason(BaseModel):
    sku_id: str
    reason: str


class RecommendResult(BaseModel):
    summary: str
    reasons: list[Reason]


_SYSTEM = "你是电商导购。基于给定候选商品数据，为每条写一句可溯源推荐理由（引用商品属性），并给一句总结。理由只描述、不编造价格数值。"


def select_top(normalized: list[dict], budget: float | None) -> list[dict]:
    """非 LLM 预算过滤：有预算时只保留到手价不高于预算的商品。"""
    pool = normalized
    if budget is not None:
        pool = [i for i in normalized if i["best"]["final_price"] <= budget]
    return pool[:_TOP_N]


def _fmt_attrs(attrs: dict) -> str:
    """属性字典 → 紧凑「值/值」（裁剪用，省去键名与引号）。"""
    return "/".join(str(v) for v in (attrs or {}).values())


def _fmt_delivery(b: dict) -> str:
    """物流时效文案；真实源（值海纳）无 eta 字段时不为 0 天，防 LLM 现编时效。"""
    eta = b.get("eta_days")
    return f"{eta}天" if eta else ""


def _fmt_prefs(prefs: dict) -> str:
    """偏好字典 → 紧凑「键=值」，跳过空字段（裁剪用）。"""
    mapping = [("category", "品类"), ("budget", "预算"), ("color", "颜色"),
               ("style", "风格"), ("material", "材质"), ("scene", "场景"),
               ("fit", "版型"), ("requirements", "要求")]
    parts = [f"{cn}={prefs[k]}" for k, cn in mapping if prefs.get(k) not in (None, "", [])]
    defaults = [
        ("preferred_colors", "长期颜色", "color"),
        ("preferred_styles", "长期风格", "style"),
        ("preferred_materials", "长期材质", "material"),
        ("avoid_preferences", "长期避雷", None),
    ]
    for key, label, current_key in defaults:
        if prefs.get(key) and (current_key is None or not prefs.get(current_key)):
            parts.append(f"{label}={'、'.join(str(v) for v in prefs[key])}")
    return " ".join(parts) if parts else str(prefs)


def build_user(prefs: dict, top: list[dict], degraded: list[str], *, compact: bool = True) -> str:
    """拼推荐上下文人消息：compact=True 走裁剪（M7），False 走 v0 全文（对照）。"""
    lines = []
    for item in top:
        b = item["best"]
        eta = _fmt_delivery(b)
        if compact:
            lines.append(
                f"- {item['sku_id']} {item['title']} {_fmt_attrs(item['attributes'])}"
                f" | {b['platform']} {b['final_price']}元 {eta}"
            )
        else:
            lines.append(
                f"- {item['sku_id']} {item['title']} 属性={item['attributes']} "
                f"最优平台={b['platform']} 到手价={b['final_price']} {eta}"
            )
    head = f"偏好 {_fmt_prefs(prefs)}" if compact else f"用户偏好={prefs}"
    user = f"{head}\n候选：\n" + "\n".join(lines)
    if degraded:
        user += "\n注：以下平台本次暂不可比（已降级，未参与最优解）：" + "、".join(degraded)
    return user


async def recommend_node(state: AgentState) -> dict:
    normalized = state.get("normalized", [])
    prefs = build_effective_preferences(
        state.get("preferences") or {}, state.get("profile_preferences") or {},
    )
    top = select_top(normalized, prefs.get("budget"))
    if not top:
        budget = prefs.get("budget")
        summary = (
            f"当前召回商品的到手价均高于预算 ¥{budget:g}，请提高预算或调整品类、风格、材质等条件。"
            if budget is not None
            else "当前没有可推荐商品，请调整购物条件后再试。"
        )
        return {
            "recommendation": {
                "summary": summary,
                "items": [],
                "degraded_sources": state.get("degraded_sources", []),
            }
        }
    user = build_user(prefs, top, state.get("degraded_sources", []), compact=True)

    structured = get_structured_llm(MAIN_MODEL, RecommendResult)
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
