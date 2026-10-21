"""澄清节点：LLM 从最新用户消息提取精简的结构化购物需求。

设计决策：
- 关键信息 = 品类 + 预算，二者缺一即 needs_clarification，追问收敛需求；
- 偏好用结构化输出（Pydantic）而非 JSON 字符串解析，避免格式抖动；
- 多轮累积：preferences 带 merge reducer（state.py）跨轮保留；
- 品类专属字段只做一次可选补充询问，不阻塞商品检索。
"""

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


def _category_kind(category: str | None) -> str | None:
    text = str(category or "")
    if any(marker in text for marker in _CLOTHING_MARKERS):
        return "clothing"
    if any(marker in text for marker in _DIGITAL_MARKERS):
        return "digital"
    return None


def _optional_question(prefs: dict) -> str:
    """类目专属字段只作为一次补充询问，不影响进入检索。"""
    kind = _category_kind(prefs.get("category"))
    if kind == "clothing" and (not prefs.get("gender") or not prefs.get("color")):
        return "更偏向男款、女款还是中性款？颜色有偏好吗？"
    if kind == "digital" and (not prefs.get("brand") or not prefs.get("use_case")):
        return "有偏好的品牌吗？主要用于拍照、游戏，还是日常使用？"
    return ""


async def clarify_node(state: AgentState) -> dict:
    structured = get_structured_llm(MAIN_MODEL, ClarifyResult)
    latest = state["messages"][-1].content if state["messages"] else ""
    prev = dict(state.get("preferences") or {})

    result: ClarifyResult = await structured.ainvoke([
        SystemMessage(_SYSTEM), HumanMessage(latest),
    ])

    # A changed product category starts a fresh set of product-specific constraints.
    new_prefs = normalize_preferences(result.preferences.model_dump())
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

    update: dict = {
        "preferences": preference_update,   # 经 merge reducer 与历史合并
        "trace_preferences": merged,
        "hop_count": state.get("hop_count", 0) + 1,
        "optional_clarify_asked": optional_asked,
    }
    if needs:
        update["needs_action"] = "clarify"
        question = result.clarify_question or _default_question(merged)
        update["clarify_question"] = question
        update["messages"] = [AIMessage(question)]
    else:
        update["needs_action"] = "shopping"
        question = "" if optional_asked else _optional_question(merged)
        update["clarify_question"] = question
        if question:
            update["messages"] = [AIMessage(question)]
            update["optional_clarify_asked"] = True
    return update
