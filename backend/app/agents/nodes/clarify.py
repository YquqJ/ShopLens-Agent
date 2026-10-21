"""澄清节点：LLM 从最新用户消息提取精简的结构化购物需求。

设计决策：
- 关键信息 = 品类 + 预算，二者缺一即 needs_clarification，追问收敛需求；
- 偏好用结构化输出（Pydantic）而非 JSON 字符串解析，避免格式抖动；
- 多轮累积：preferences 带 merge reducer（state.py）跨轮保留；
- 追问必须带上已确认需求与上一轮问句，否则「预算 100」这类省略回答抽不出字段，
  会与已填字段一起触发同一句追问无限循环；
- 品类专属字段只做一次可选补充询问，不阻塞商品检索。
"""
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.llm import MAIN_MODEL, get_structured_llm
from app.agents.state import AgentState, merge_preferences


class Preferences(BaseModel):
    category: str | None = Field(None, description="商品品类，如 手机/耳机/外套/厨房用品")
    budget: float | None = Field(None, description="预算（元）")
    requirements: str | None = Field(None, description="无法归入闭集字段的功能/描述需求，如 显瘦一点、透气（无则留空）")
    gender: str | None = Field(None, description="服饰性别偏好，如 男款/女款/中性（无则留空）")
    color: str | None = Field(None, description="服饰颜色偏好，如 黑色/深蓝（无则留空）")
    brand: str | None = Field(None, description="数码商品品牌偏好（无则留空）")
    use_case: str | None = Field(None, description="数码商品主要用途，如 拍照/游戏/日常使用（无则留空）")


class ClarifyResult(BaseModel):
    needs_clarification: bool = Field(..., description="是否缺少关键信息需追问")
    preferences: Preferences
    clarify_question: str = Field("", description="追问句（needs_clarification=True 时填）")


_SYSTEM = (
    "你是导购澄清助手。从用户一句话里提取精简购物需求：通用字段为品类、预算、其他要求；服饰只提取性别和颜色；数码只提取品牌和主要用途。"
    "拿不准的字段留空，不能把服饰字段填到数码需求，也不能把数码字段填到服饰需求。"
    "如果用户本轮更换了商品品类，视为新的商品需求；旧品类的预算、品牌、颜色、性别、用途及其他要求不可自动沿用，只有本轮再次明确提到时才填写。"
    "关键信息=品类+预算，缺任一即 needs_clarification=True。"
)

_CATEGORY_SCOPED_FIELDS = (
    "budget", "brand", "color", "gender", "use_case", "requirements",
)

_PREFERENCE_FIELDS = frozenset(Preferences.model_fields)
_CLOTHING_MARKERS = ("服饰", "衣", "裤", "裙", "鞋", "帽", "袜", "外套", "衬衫", "卫衣", "T恤")
_DIGITAL_MARKERS = ("数码", "手机", "电脑", "笔记本", "平板", "耳机", "相机", "手表")
_COLOR_TERMS = (
    "黑色", "白色", "蓝色", "红色", "灰色", "绿色", "黄色", "粉色", "紫色", "橙色",
    "深蓝", "浅蓝", "米色", "卡其",
)
_GENDER_TERMS = ("男款", "女款", "中性款", "中性", "男士", "女士")
# 追问轮次上限：达到上限后按已确认需求继续检索，避免缺字段时无限追问。
MAX_CLARIFY_ROUNDS = 3

_BUDGET_KEYWORD_RE = re.compile(r"(?:预算|价位|价格)\s*[:：]?\s*(\d{2,6}(?:\.\d+)?)")
_BUDGET_BARE_RE = re.compile(r"^\s*(\d{2,6}(?:\.\d+)?)\s*(?:元|块|以内|以下|左右|封顶)?\s*$")


def normalize_preferences(raw: dict) -> dict:
    """只保留精简需求字段，并丢弃空值。"""
    normalized = {
        key: value for key, value in (raw or {}).items()
        if key in _PREFERENCE_FIELDS and value not in (None, "", [])
    }
    return normalized


def _missing(prefs: dict) -> list[str]:
    """Fields required before searching any product category."""
    missing = []
    if not prefs.get("category"):
        missing.append("想买什么商品")
    if prefs.get("budget") is None:
        missing.append("预算大概多少")
    return missing


def _default_question(prefs: dict) -> str:
    """LLM 未填问句时，按合并后缺失的关键字段确定性拼默认追问。"""
    missing = _missing(prefs)
    if missing:
        return "为了更精准地推荐，" + "、".join(missing) + "？"
    return "能否再补充一下你的偏好？"


def _known_summary(prefs: dict) -> str:
    """把已确认需求拼成简短中文，供追问时告知用户进度。"""
    parts = []
    if prefs.get("category"):
        parts.append(str(prefs["category"]))
    if prefs.get("budget") is not None:
        budget = float(prefs["budget"])
        parts.append(f"预算 ¥{int(budget) if budget.is_integer() else budget:.2f}")
    for key in ("gender", "color", "brand", "use_case", "requirements"):
        if prefs.get(key):
            parts.append(str(prefs[key]))
    return "、".join(parts)


def _missing_question(prefs: dict) -> str:
    """只针对缺失的关键字段追问，并回显已确认需求。"""
    missing = _missing(prefs)
    if not missing:
        return ""
    known = _known_summary(prefs)
    question = "还需要确认：" + "、".join(missing) + "？"
    return f"已了解 {known}。{question}" if known else question


def _mentions_known_field(question: str, prefs: dict) -> bool:
    """判断 LLM 问句是否在追问已经确认的关键字段。"""
    text = question or ""
    if prefs.get("category") and any(marker in text for marker in ("品类", "什么商品", "想买什么", "哪类")):
        return True
    if prefs.get("budget") is not None and "预算" in text:
        return True
    return False


def _clarify_question(
    llm_question: str,
    prefs: dict,
    previous_question: str | None,
) -> str:
    """优先用模型问句，但不得追问已确认字段或原样重复上一句。"""
    candidate = (llm_question or "").strip()
    if candidate and not _mentions_known_field(candidate, prefs) and candidate != (previous_question or "").strip():
        return candidate
    return _missing_question(prefs)


def _backfill_answer(text: str, prefs: dict) -> dict:
    """从「预算 100」「100 元」「男款」这类省略回答补齐字段。

    模型对脱离上下文的短答经常抽不出字段；这里做确定性兜底，避免同一问题反复追问。
    """
    answer = (text or "").strip()
    if not answer or len(answer) > 30:
        return {}
    filled: dict = {}
    if prefs.get("budget") is None:
        match = _BUDGET_KEYWORD_RE.search(answer) or _BUDGET_BARE_RE.match(answer)
        if match:
            filled["budget"] = float(match.group(1))
    if not prefs.get("gender"):
        gender = next((term for term in _GENDER_TERMS if term in answer), None)
        if gender:
            filled["gender"] = gender
    if not prefs.get("color"):
        color = next((term for term in _COLOR_TERMS if term in answer), None)
        if color:
            filled["color"] = color
    return filled


def _context_prompt(prefs: dict, previous_question: str | None) -> str:
    """给模型提供已确认需求与上一轮追问，避免短答脱离上下文。"""
    lines = []
    known = _known_summary(prefs)
    if known:
        lines.append(f"已确认需求：{known}")
    if previous_question:
        lines.append(f"上一轮追问：{previous_question}")
    lines.append("只提取本轮回答中的新信息；已确认字段不要重复填写，也不要再追问它们。")
    return "\n".join(lines)


def _category_kind(category: str | None) -> str | None:
    text = str(category or "")
    if any(marker in text for marker in _CLOTHING_MARKERS):
        return "clothing"
    if any(marker in text for marker in _DIGITAL_MARKERS):
        return "digital"
    return None


def _optional_question(prefs: dict) -> str:
    """类目专属字段只作为一次补充询问，不影响进入检索；只问确实缺失的字段。"""
    kind = _category_kind(prefs.get("category"))
    if kind == "clothing":
        parts = []
        if not prefs.get("gender"):
            parts.append("更偏向男款、女款还是中性款？")
        if not prefs.get("color"):
            parts.append("颜色有偏好吗？")
        return "".join(parts)
    if kind == "digital":
        parts = []
        if not prefs.get("brand"):
            parts.append("有偏好的品牌吗？")
        if not prefs.get("use_case"):
            parts.append("主要用于拍照、游戏，还是日常使用？")
        return "".join(parts)
    return ""


async def clarify_node(state: AgentState) -> dict:
    structured = get_structured_llm(MAIN_MODEL, ClarifyResult)
    latest = state["messages"][-1].content if state["messages"] else ""
    prev = dict(state.get("preferences") or {})
    previous_question = str(state.get("clarify_question") or "")

    result: ClarifyResult = await structured.ainvoke([
        SystemMessage(_SYSTEM),
        SystemMessage(_context_prompt(prev, previous_question)),
        HumanMessage(latest),
    ])

    # A changed product category starts a fresh set of product-specific constraints.
    new_prefs = normalize_preferences(result.preferences.model_dump())
    for key, value in _backfill_answer(str(latest), {**prev, **new_prefs}).items():
        new_prefs.setdefault(key, value)
    category_changed = bool(
        new_prefs.get("category")
        and prev.get("category")
        and str(new_prefs["category"]).strip() != str(prev["category"]).strip()
    )
    preference_update = dict(new_prefs)
    if category_changed:
        preference_update["__clear_preference_fields__"] = _CATEGORY_SCOPED_FIELDS
    merged = merge_preferences(prev, preference_update)

    # 关键信息按「合并后」判断，而非只信 LLM 对最新消息的 needs_clarification。
    needs = bool(_missing(merged))
    optional_asked = False if category_changed else bool(state.get("optional_clarify_asked"))
    rounds = int(state.get("hop_count") or 0) + 1

    update: dict = {
        "preferences": preference_update,   # 经 merge reducer 与历史合并
        "trace_preferences": merged,
        "hop_count": rounds,
        "optional_clarify_asked": optional_asked,
    }
    if needs and rounds < MAX_CLARIFY_ROUNDS:
        update["needs_action"] = "clarify"
        question = _clarify_question(result.clarify_question, merged, previous_question) or _default_question(merged)
        update["clarify_question"] = question
        update["messages"] = [AIMessage(question)]
    else:
        # 关键信息仍缺失但已达追问上限：按已确认条件继续检索，避免死循环。
        update["needs_action"] = "shopping"
        # 刚回答完上一轮追问时不再追加可选问题，避免「问完一个又问一个」的连环追问。
        question = "" if (optional_asked or needs or previous_question) else _optional_question(merged)
        update["clarify_question"] = question
        if question:
            update["messages"] = [AIMessage(question)]
            update["optional_clarify_asked"] = True
    return update
