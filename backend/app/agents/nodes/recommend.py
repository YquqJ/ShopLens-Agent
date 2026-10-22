"""推荐节点：LLM 基于值得买真实商品字段生成可溯源推荐。

设计决策：
- 数值和媒体字段（sku_id/title/final_price/platform/attributes/image_url/url）一律从候选事实取，
  只让 LLM 生成本文「理由/总结」，杜绝价格被 LLM 现编；
- 有预算时先做非 LLM 过滤（best 到手价 ≤ 预算），选不出再退回全部，保证 ≥1 条；
- 只向模型提供紧凑的候选事实和 evidence_id，避免把外部原始响应直接交给模型。
"""
import json
import math
import re
import uuid
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from pydantic import BaseModel

from app.agents.llm import MAIN_MODEL, get_structured_llm
from app.agents.state import AgentState

_TOP_N = 3


class Reason(BaseModel):
    sku_id: str
    reason: str
    evidence_ids: list[str] = []


class ComparisonDimension(BaseModel):
    field: str
    evidence_by_sku: dict[str, str]


class PriceAssessment(BaseModel):
    sku_id: str
    reference_label: str
    assessment: Literal["wait", "good_time", "neutral"]
    evidence_ids: list[str] = []


class ComparisonResult(BaseModel):
    summary: str
    best_sku_id: str | None = None
    dimensions: list[ComparisonDimension] = []
    reasons: list[Reason] = []
    price_assessments: list[PriceAssessment] = []


RecommendResult = ComparisonResult


_SYSTEM = (
    "你是电商导购。只能基于候选事实和 evidence_id 做横向比较。"
    "只返回候选中真实存在的 sku_id 和 evidence_id；不要输出价格、平台、属性值或来源文本。"
    "dimension 的 field 必须是同一字段，evidence_by_sku 至少覆盖两个候选。"
    "每条 reasons 必须至少引用一个属于该 sku_id 的 evidence_id。"
    "price_assessments 只处理标题为常卖价的价格标签，必须引用该商品的当前价格和价格标签 evidence_id。"
    "当前价高于常卖价时 assessment=wait，当前价低于常卖价时 assessment=good_time，二者相等时 assessment=neutral。"
    "没有足够横向证据时 best_sku_id 必须为 null。"
)

_PRICE_VALUE_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?")
_REGULAR_PRICE_LABEL = "常卖价"


def _price_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        match = _PRICE_VALUE_PATTERN.search(value.replace(",", ""))
        if not match:
            return None
        number = float(match.group())
    else:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _build_price_timing_context(top: list[dict], evidence: dict[str, list[dict]]) -> list[dict]:
    """Join current prices with the single source-backed regular-price tag."""
    contexts = []
    for item in top:
        sku_id = item.get("sku_id")
        facts = evidence.get(sku_id, [])
        page_fact = next((fact for fact in facts if fact.get("field") == "page_price"), None)
        tags_fact = next((fact for fact in facts if fact.get("field") == "price_tags"), None)
        current_price = _price_number(page_fact.get("value") if page_fact else item.get("page_price"))
        tags = tags_fact.get("value") if tags_fact else []
        if isinstance(tags, dict):
            tags = [tags]
        if not isinstance(tags, list) or current_price is None or not tags_fact or not page_fact:
            continue
        regular_tag = next(
            (
                tag for tag in tags
                if isinstance(tag, dict)
                and str(next((tag.get(key) for key in ("title", "name", "label") if tag.get(key)), "")).replace(" ", "") == _REGULAR_PRICE_LABEL
            ),
            None,
        )
        if regular_tag is None:
            continue
        reference_price = _price_number(next((regular_tag.get(key) for key in ("price", "value") if regular_tag.get(key) is not None), None))
        if reference_price is None:
            continue
        contexts.append({
            "sku_id": sku_id,
            "current_price": current_price,
            "reference_label": _REGULAR_PRICE_LABEL,
            "reference_price": reference_price,
            "reference_kind": "reference",
            "evidence_ids": [page_fact["evidence_id"], tags_fact["evidence_id"]],
        })
    return contexts


def _expected_price_assessment(context: dict) -> str:
    if context["current_price"] > context["reference_price"]:
        return "wait"
    if context["current_price"] < context["reference_price"]:
        return "good_time"
    return "neutral"


def _validate_price_assessments(result: ComparisonResult, contexts: list[dict]) -> list[dict]:
    """Return source-backed timing advice, using the model only as a validated hint."""
    proposed = {
        (assessment.sku_id, assessment.reference_label): assessment
        for assessment in result.price_assessments
    }
    insights = []
    for context in contexts:
        expected = _expected_price_assessment(context)
        assessment = proposed.get((context["sku_id"], context["reference_label"]))
        evidence_matches = assessment is not None and set(context["evidence_ids"]).issubset(assessment.evidence_ids)
        # Source values are authoritative. If the model omits the item or cites unrelated
        # evidence, still surface the deterministic assessment from the verified facts.
        validated_assessment = (
            assessment.assessment
            if evidence_matches and assessment.assessment == expected
            else expected
        )
        if validated_assessment != "neutral":
            insights.append({
                **context,
                "assessment": validated_assessment,
            })
    return insights


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
        "price_insights": _validate_price_assessments(
            result,
            _build_price_timing_context(candidates, evidence),
        ),
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
    if "good_comments_share" in fields or "price_tags" in fields:
        reason = "该商品具备价格与口碑相关的真实源证据；当前横向属性证据不足，建议结合你的需求选择。"
    else:
        reason = "该商品的标题、价格和商城信息来自值得买真实源，可核验；当前横向属性证据不足，建议结合需求选择。"
    return {"sku_id": item["sku_id"], "reason": reason, "evidence_ids": evidence_ids}


def _fmt_attrs(attrs: dict) -> str:
    """属性字典 → 紧凑「值/值」（裁剪用，省去键名与引号）。"""
    return "/".join(str(v) for v in (attrs or {}).values())


def _fmt_prefs(prefs: dict) -> str:
    """偏好字典 → 紧凑「键=值」，跳过空字段（裁剪用）。"""
    mapping = [("category", "品类"), ("budget", "预算"), ("brand", "品牌"),
               ("use_case", "用途"), ("color", "颜色"), ("gender", "性别"),
               ("requirements", "要求")]
    parts = [f"{cn}={prefs[k]}" for k, cn in mapping if prefs.get(k) not in (None, "", [])]
    return " ".join(parts) if parts else str(prefs)


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
    for key in ("category", "brand", "use_case", "gender", "color"):
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
    return await _recommend_with_evidence(state)


async def _recommend_with_evidence(state: AgentState) -> dict:
    prefs = dict(state.get("preferences") or {})
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
        summary = (
            "没有找到符合当前条件的商品，请放宽筛选条件或换个关键词后重试。"
            if not candidates
            else "当前没有预算内且价格可核验的商品。"
        )
        recommendation = {"summary": summary, "items": []}
        return {
            "recommendation": recommendation,
            "recommendation_history": _recommendation_history_update(state, recommendation),
        }
    evidence = state.get("product_evidence") or {}
    structured = get_structured_llm(MAIN_MODEL, ComparisonResult)
    price_context = _build_price_timing_context(top, evidence)
    prompt = (
        f"偏好={_fmt_prefs(prefs)}\n"
        f"候选={_build_evidence_items(top, evidence)}\n"
        f"价格时机参考={json.dumps(price_context, ensure_ascii=False)}\n"
        f"只可引用这些证据 ID：\n{_evidence_context(top, evidence)}"
    )
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
        "price_insights": validated["price_insights"],
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
