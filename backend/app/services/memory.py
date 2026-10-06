"""用户长期偏好的纯函数工具。"""
from copy import deepcopy
from typing import Any


DURABLE_MEMORY_KEYS = frozenset({
    "preferred_colors",
    "preferred_styles",
    "preferred_materials",
    "preferred_genders",
    "avoid_preferences",
})

_TAG_LABELS = (
    ("preferred_colors", "颜色"),
    ("preferred_styles", "风格"),
    ("preferred_materials", "材质"),
    ("preferred_genders", "性别"),
    ("avoid_preferences", "避雷"),
)


def merge_memory(profile: dict, update: dict) -> dict:
    """合并长期偏好，并忽略会话临时字段与未知字段。"""
    merged = {
        key: deepcopy(value)
        for key, value in profile.items()
        if key in DURABLE_MEMORY_KEYS
    }
    forget = update.get("forget", [])
    if isinstance(forget, str):
        forget = [forget]
    for key in forget:
        if key in DURABLE_MEMORY_KEYS:
            merged.pop(key, None)

    for key, value in update.items():
        if key not in DURABLE_MEMORY_KEYS or value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            existing = merged.get(key, [])
            existing_values = existing if isinstance(existing, list) else [existing]
            merged[key] = list(dict.fromkeys([*existing_values, *value]))
        else:
            merged[key] = deepcopy(value)
    return merged


def _display_value(value: Any) -> str:
    if isinstance(value, dict):
        minimum = value.get("min")
        maximum = value.get("max")
        if minimum is not None and maximum is not None:
            return f"¥{minimum}-{maximum}"
        if minimum is not None:
            return f"¥{minimum} 起"
        if maximum is not None:
            return f"¥{maximum} 以内"
        return ""
    if isinstance(value, list):
        return "、".join(str(item) for item in value if item not in (None, ""))
    return str(value) if value not in (None, "") else ""


def memory_tags(profile: dict) -> list[str]:
    """将已保存的非空偏好转换为稳定、可展示的标签。"""
    tags: list[str] = []
    for key, label in _TAG_LABELS:
        value = _display_value(profile.get(key))
        if value:
            tags.append(f"{label}：{value}")
    return tags
