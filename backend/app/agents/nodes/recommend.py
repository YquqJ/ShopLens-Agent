"""推荐节点：LLM 基于归一结果生成可溯源推荐。

设计决策：
- 数值和媒体字段（sku_id/title/final_price/platform/attributes/image_url/url）一律从数据（normalized）取，
  只让 LLM 生成本文「理由/总结」，杜绝价格被 LLM 现编；
- 有预算时先做非 LLM 过滤（best 到手价 ≤ 预算），选不出再退回全部，保证 ≥1 条；
- M7 结构化裁剪：喂给 LLM 的上下文从「字典 repr 全文」改为紧凑 key=value 摘要
  （build_user(compact=True)），不喂 normalize 前的原始报价原文。
"""
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from pydantic import BaseModel

from app.agents.llm import MAIN_MODEL, get_structured_llm
from app.agents.state import AgentState
from app.agents.nodes.memory import build_effective_preferences

_TOP_N = 3


class Reason(BaseModel):
    sku_id: str
    reason: str
    evidence_ids: list[str] = []


class ComparisonDimension(BaseModel):
    field: str
    evidence_by_sku: dict[str, str]


class ComparisonResult(BaseModel):
    summary: str
    best_sku_id: str | None = None
    dimensions: list[ComparisonDimension] = []
    reasons: list[Reason] = []


RecommendResult = ComparisonResult


_SYSTEM = (
    "你是电商导购。只能基于候选事实和 evidence_id 做横向比较。"
    "只返回候选中真实存在的 sku_id 和 evidence_id；不要输出价格、平台、属性值或来源文本。"
    "dimension 的 field 必须是同一字段，evidence_by_sku 至少覆盖两个候选。"
    "每条 reasons 必须至少引用一个属于该 sku_id 的 evidence_id。"
    "没有足够横向证据时 best_sku_id 必须为 null。"
)


def validate_comparison(result: ComparisonResult, candidates: list[dict], evidence: dict[str, list[dict]]) -> dict:
    """Validate model references and backfill displayed values from server facts."""
    sku_ids = {candidate.get("sku_id") for candidate in candidates}
    facts_by_id = {
        fact.get("evidence_id"): fact
        for sku_facts in evidence.values()
        for fact in sku_facts
        if fact.get("evidence_id")
    }
    dimensions = []
    for dimension in result.dimensions:
        field = dimension.field
        refs = dimension.evidence_by_sku
        valid = {}
        values = {}
        for sku_id, evidence_id in refs.items():
            fact = facts_by_id.get(evidence_id)
            if sku_id not in sku_ids or fact is None or fact.get("sku_id") != sku_id or fact.get("field") != field:
                continue
            valid[sku_id] = evidence_id
            values[sku_id] = fact.get("value")
        if len(valid) >= 2:
            dimensions.append({"field": field, "evidence_by_sku": valid, "values": values})

    reasons = []
    for reason in result.reasons:
        if reason.sku_id not in sku_ids:
            continue
        valid_ids = [evidence_id for evidence_id in reason.evidence_ids if evidence_id in facts_by_id and facts_by_id[evidence_id].get("sku_id") == reason.sku_id]
        if valid_ids:
            reasons.append({"sku_id": reason.sku_id, "reason": reason.reason, "evidence_ids": valid_ids})

    best_sku_id = result.best_sku_id
    if best_sku_id not in sku_ids or not any(best_sku_id in dimension["evidence_by_sku"] and len(dimension["evidence_by_sku"]) >= 2 for dimension in dimensions):
        best_sku_id = None
    return {
        "summary": result.summary,
        "best_sku_id": best_sku_id,
        "dimensions": dimensions,
        "reasons": reasons,
    }


def _evidence_context(top: list[dict], evidence: dict[str, list[dict]]) -> str:
    lines = []
    for item in top:
        sku_id = item["sku_id"]
        facts = evidence.get(sku_id, [])
        compact = "; ".join(f"{fact['evidence_id']}={fact['field']}" for fact in facts)
        lines.append(f"- {sku_id} {item.get('title', '')} evidence: {compact}")
    return "\n".join(lines)


def _build_evidence_items(top: list[dict], evidence: dict[str, list[dict]]) -> list[dict]:
    items = []
    for item in top:
        facts = evidence.get(item["sku_id"], [])
        items.append({
            "sku_id": item["sku_id"],
            "title": item.get("title"),
            "evidence": [{
                "evidence_id": fact["evidence_id"],
                "field": fact["field"],
                "value": fact.get("value"),
            } for fact in facts],
        })
    return items


def _fallback_reason(item: dict, facts: list[dict]) -> dict:
    """Keep cards explainable when the model omits a valid reason citation."""
    evidence_ids = [fact["evidence_id"] for fact in facts if fact.get("evidence_id")][:3]
    fields = {fact.get("field") for fact in facts}
    if any(
        str(fact.get("field", "")).startswith("entity_")
        or ":kg:" in str(fact.get("evidence_id"))
        for fact in facts
    ):
        reason = "该商品有结构化商品属性证据可核验；当前横向证据不足，暂不宣称它绝对最优。"
    elif "good_comments_share" in fields or "price_tags" in fields:
        reason = "该商品具备价格与口碑相关的真实源证据；当前横向属性证据不足，建议结合你的需求选择。"
    else:
        reason = "该商品的标题、价格和商城信息来自值得买真实源，可核验；当前横向属性证据不足，建议结合需求选择。"
    return {"sku_id": item["sku_id"], "reason": reason, "evidence_ids": evidence_ids}


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
               ("fit", "版型"), ("gender", "性别"),
               ("requirements", "要求")]
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


def build_recommendation_preview(top: list[dict], degraded: list[str], intro: str = "") -> dict:
    """在 LLM 生成理由前发送可验证的商品事实，缩短首张推荐卡片 TTFT。"""
    return {
        "summary": "",
        "items": [{
            "sku_id": item["sku_id"],
            "title": item["title"],
            "final_price": item["best"]["final_price"],
            "platform": item["best"]["platform"],
            "attributes": item["attributes"],
            "image_url": item.get("image_url", ""),
            "url": item.get("url", ""),
            "reason": "",
        } for item in top],
        "degraded_sources": degraded,
        "recommendation_intro": intro,
    }


def _recommendation_history_update(state: AgentState, recommendation: dict, intro: str = "") -> list[dict]:
    """把最终推荐按本轮 turn_id 追加，供历史会话逐轮恢复卡片。"""
    if not recommendation.get("items") and not recommendation.get("summary"):
        return []
    turn_id = state.get("turn_id")
    if turn_id and any(entry.get("turn_id") == turn_id for entry in state.get("recommendation_history", [])):
        return []
    return [{"turn_id": turn_id, "recommendation": recommendation, "intro": intro}]


def _format_recommendation_intro(prefs: dict, count: int) -> str:
    """生成推荐卡片前的结构化需求确认语句。"""
    parts = []
    for key in ("category", "gender", "color", "style", "material", "scene", "fit"):
        value = prefs.get(key)
        if value not in (None, "", []):
            parts.append(str(value))
    if prefs.get("budget") is not None:
        budget = float(prefs["budget"])
        budget_text = str(int(budget)) if budget.is_integer() else f"{budget:.2f}"
        parts.append(f"预算 ¥{budget_text}")
    if prefs.get("requirements"):
        parts.append(str(prefs["requirements"]))
    summary = " · ".join(parts) if parts else "当前需求"
    return f"根据您的“{summary}”需求，为你推荐{count}款候选"


def _stream_preview(preview: dict) -> None:
    """节点被单测直接调用时没有 LangGraph runtime，故无 writer 时静默跳过。"""
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer({"event": "recommendation_preview", "recommendation": preview})


async def recommend_node(state: AgentState) -> dict:
    if state.get("candidates") and state.get("product_evidence"):
        return await _recommend_with_evidence(state)
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
        recommendation = {
            "summary": summary,
            "items": [],
            "degraded_sources": state.get("degraded_sources", []),
        }
        return {
            "recommendation": recommendation,
            "recommendation_history": _recommendation_history_update(state, recommendation),
        }
    intro = _format_recommendation_intro(prefs, len(top))
    _stream_preview(build_recommendation_preview(top, state.get("degraded_sources", []), intro))
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
            "image_url": item.get("image_url", ""),
            "url": item.get("url", ""),
            "reason": r.reason,                     # 文本取 LLM（挂属性，可溯源）
        })
    recommendation = {"summary": res.summary, "items": items, "degraded_sources": state.get("degraded_sources", [])}
    intro = _format_recommendation_intro(prefs, len(items))
    return {
        "recommendation": recommendation,
        "recommendation_intro": intro,
        "recommendation_history": _recommendation_history_update(state, recommendation, intro),
    }


async def _recommend_with_evidence(state: AgentState) -> dict:
    prefs = build_effective_preferences(state.get("preferences") or {}, state.get("profile_preferences") or {})
    candidates = list(state.get("candidates") or [])
    budget = prefs.get("budget")
    top = []
    for candidate in candidates:
        try:
            price = float(candidate.get("page_price"))
        except (TypeError, ValueError):
            continue
        if budget is not None and price > float(budget):
            continue
        top.append(candidate)
        if len(top) >= _TOP_N:
            break
    if not top:
        recommendation = {"summary": "当前没有预算内且价格可核验的商品。", "items": []}
        return {
            "recommendation": recommendation,
            "recommendation_history": _recommendation_history_update(state, recommendation),
        }
    evidence = state.get("product_evidence") or {}
    structured = get_structured_llm(MAIN_MODEL, ComparisonResult)
    prompt = f"偏好={_fmt_prefs(prefs)}\n候选={_build_evidence_items(top, evidence)}\n只可引用这些证据 ID：\n{_evidence_context(top, evidence)}"
    result: ComparisonResult = await structured.ainvoke([SystemMessage(_SYSTEM), HumanMessage(prompt)])
    validated = validate_comparison(result, top, evidence)
    by_sku = {item["sku_id"]: item for item in top}
    ordered = ([by_sku[validated["best_sku_id"]]] if validated["best_sku_id"] else []) + [item for item in top if item["sku_id"] != validated["best_sku_id"]]
    reasons = {reason["sku_id"]: reason for reason in validated["reasons"]}
    items = []
    for item in ordered:
        reason = reasons.get(item["sku_id"]) or _fallback_reason(item, evidence.get(item["sku_id"], []))
        items.append({
            "sku_id": item["sku_id"],
            "title": item.get("title"),
            "final_price": item.get("page_price"),
            "platform": item.get("mall"),
            "attributes": item.get("attributes", {}),
            "image_url": item.get("image_url") or item.get("picture", ""),
            "url": item.get("url", ""),
            "reason": reason.get("reason", ""),
            "evidence_ids": reason.get("evidence_ids", []),
        })
    recommendation = {
        "summary": validated["summary"] if validated["best_sku_id"] else "当前证据不足以确定最优商品。" if not validated["dimensions"] else validated["summary"],
        "best_sku_id": validated["best_sku_id"],
        "dimensions": validated["dimensions"],
        "items": items,
        "degraded_sources": state.get("degraded_sources", []),
    }
    intro = _format_recommendation_intro(prefs, len(items))
    return {
        "recommendation": recommendation,
        "recommendation_intro": intro,
        "recommendation_history": _recommendation_history_update(state, recommendation, intro),
        "comparison": validated,
        "order_intent_id": state.get("order_intent_id") or str(uuid.uuid4()),
    }
