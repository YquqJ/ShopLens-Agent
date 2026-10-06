"""受控的跨会话偏好记忆节点与确定性命令解析。"""
from __future__ import annotations

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.services import profile
from app.services.memory import DURABLE_MEMORY_KEYS


_FIELD_TERMS = {
    "preferred_colors": ("黑色", "白色", "深蓝", "米白", "卡其"),
    # 通勤同时是商品场景，V1 不将它解析成跨会话风格偏好。
    "preferred_styles": ("简约", "街头", "运动", "复古"),
    "preferred_materials": ("纯棉", "亚麻", "牛仔", "抓绒", "羊毛", "棉服", "真皮", "帆布", "网面"),
    "preferred_genders": ("男款", "女款"),
    "preferred_sizes": ("S码", "M码", "L码", "XL码", "36码", "37码", "38码", "39码", "40码", "41码", "42码", "43码"),
}
_FIELD_LABELS = {
    "preferred_colors": ("颜色",),
    "preferred_styles": ("风格",),
    "preferred_materials": ("材质",),
    "preferred_genders": ("性别", "男款", "女款"),
    "preferred_sizes": ("尺码", "码数"),
    "avoid_preferences": ("避雷", "不喜欢"),
}
_SIZE_CANONICAL = {
    "S码": "S", "M码": "M", "L码": "L", "XL码": "XL",
    **{f"{size}码": size for size in range(36, 44)},
}


def _matched_terms(text: str) -> dict[str, list[str]]:
    return {
        key: [_SIZE_CANONICAL.get(term, term) for term in terms if term in text]
        for key, terms in _FIELD_TERMS.items()
        if any(term in text for term in terms)
    }


def parse_memory_command(text: str) -> dict:
    """只接受明确、非临时的用户授权命令，绝不推断长期偏好。"""
    text = (text or "").strip()
    if not text or "这次" in text:
        return {}

    if "忘记我的" in text:
        matched = _matched_terms(text)
        if matched:
            return {"remove_values": matched}
        named_fields = [
            key for key, labels in _FIELD_LABELS.items()
            if any(label in text for label in labels)
        ]
        if named_fields:
            return {"forget": sorted(set(named_fields))}
        return {}

    is_remember = "记住我" in text
    is_future_priority = "以后" in text and "优先" in text and text.index("以后") < text.index("优先")
    is_update = "更新我的" in text
    if not (is_remember or is_future_priority or is_update):
        return {}

    matched = _matched_terms(text)
    if not matched:
        return {}

    if is_remember and "不喜欢" in text:
        avoided = [term for values in matched.values() for term in values]
        return {"avoid_preferences": avoided}

    return matched


def build_effective_preferences(session_preferences: dict, profile_preferences: dict) -> dict:
    """合成推荐上下文，保留会话值为高优先级、画像值为低优先级默认。"""
    effective = dict(session_preferences or {})
    for key, value in (profile_preferences or {}).items():
        if key in DURABLE_MEMORY_KEYS and value not in (None, "", []):
            effective[key] = value
    return effective


def _latest_user_message(state: AgentState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def profile_key_from_config(config: RunnableConfig | dict | None) -> str:
    """优先使用浏览器稳定画像 ID，非浏览器调用才隔离到当前线程。"""
    configurable = (config or {}).get("configurable", {})
    profile_id = configurable.get("profile_id")
    if isinstance(profile_id, str) and profile_id.strip():
        return profile_id.strip()
    thread_id = configurable.get("thread_id")
    if isinstance(thread_id, str) and thread_id.strip():
        return thread_id.strip()
    raise ValueError("profile_id 或 thread_id 不能为空")


async def load_memory_node(state: AgentState, config: RunnableConfig) -> dict:
    """每轮开始读取持久画像；事件只记录键名与数量，适合前端 Trace。"""
    preferences = await profile.get_preferences(profile_key_from_config(config))
    return {
        "profile_preferences": preferences,
        "memory_events": [{
            "type": "memory_read",
            "keys": sorted(preferences),
            "count": len(preferences),
        }],
    }


async def capture_memory_node(state: AgentState, config: RunnableConfig) -> dict:
    """仅执行用户明确要求的保存、更新或遗忘，不保存预算和场景。"""
    updates = parse_memory_command(_latest_user_message(state))
    if not updates:
        return {}

    preferences = await profile.apply_updates(updates, profile_key_from_config(config))
    forgotten = updates.get("forget", [])
    removed = updates.get("remove_values", {})
    written = [key for key in updates if key not in {"forget", "remove_values"}]
    action = "forget" if forgotten or removed else "write"
    keys = sorted([*written, *forgotten, *removed])
    return {
        "profile_preferences": preferences,
        "memory_events": [{"type": "memory_write", "action": action, "keys": keys}],
    }
