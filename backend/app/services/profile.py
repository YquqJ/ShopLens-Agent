"""用户授权的长期偏好持久化服务。"""
from copy import deepcopy
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import ProfileMemory
from app.db.session import async_session_factory
from app.services.memory import DURABLE_MEMORY_KEYS

_DEFAULT_SOURCE = "explicit_command"
_META_KEYS = frozenset({"forget", "source", "evidence"})


def normalize_updates(updates: dict[str, Any]) -> tuple[dict[str, Any], set[str], dict[str, Any]]:
    """过滤写入载荷，并将全局或单字段来源信息规范为独立映射。"""
    values: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    if "source" in updates or "evidence" in updates:
        provenance = {
            "source": updates.get("source", _DEFAULT_SOURCE),
            "evidence": updates.get("evidence"),
        }
    forget = updates.get("forget", [])
    if isinstance(forget, str):
        forget = [forget]
    forgotten = {
        key for key in forget
        if isinstance(key, str) and key in DURABLE_MEMORY_KEYS
    }

    for key, raw_value in updates.items():
        if key in _META_KEYS or key not in DURABLE_MEMORY_KEYS:
            continue
        value = raw_value
        if isinstance(raw_value, dict) and "value" in raw_value:
            value = raw_value["value"]
            provenance[key] = {
                "source": raw_value.get("source", provenance.get("source", _DEFAULT_SOURCE)),
                "evidence": raw_value.get("evidence", provenance.get("evidence")),
            }
        if value is None or value == []:
            continue
        values[key] = deepcopy(value)

    # 与 memory.merge_memory 的语义一致：同一次更新中的新值优先于忘记指令。
    forgotten.difference_update(values)
    return values, forgotten, provenance


def _field_provenance(key: str, provenance: dict[str, Any]) -> tuple[str, str | None]:
    field = provenance.get(key)
    if isinstance(field, dict):
        source = field.get("source")
        evidence = field.get("evidence")
    else:
        source = provenance.get("source")
        evidence = provenance.get("evidence")
    return str(source or _DEFAULT_SOURCE), str(evidence) if evidence is not None else None


def _normalize_remove_values(updates: dict[str, Any]) -> dict[str, list[Any]]:
    """过滤值级遗忘载荷；仅列表型长期偏好可被逐项删除。"""
    raw = updates.get("remove_values")
    if not isinstance(raw, dict):
        return {}
    removals: dict[str, list[Any]] = {}
    for key, values in raw.items():
        if key not in DURABLE_MEMORY_KEYS:
            continue
        if isinstance(values, list) and values:
            removals[key] = deepcopy(values)
    return removals


async def get_preferences(profile_key: str) -> dict:
    """读取画像的干净偏好值，不暴露持久化元数据。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ProfileMemory)
            .where(ProfileMemory.profile_key == profile_key)
            .order_by(ProfileMemory.key)
        )
        rows = result.scalars().all()
    return {
        row.key: deepcopy(row.value)
        for row in rows
        if row.key in DURABLE_MEMORY_KEYS
    }


async def apply_updates(updates: dict[str, Any], profile_key: str) -> dict:
    """替换指定长期偏好、删除遗忘字段，并返回保存后的干净画像。"""
    values, forgotten, provenance = normalize_updates(updates)
    removals = _normalize_remove_values(updates)
    async with async_session_factory() as session:
        if forgotten:
            await session.execute(
                delete(ProfileMemory).where(
                    ProfileMemory.profile_key == profile_key,
                    ProfileMemory.key.in_(forgotten),
                )
            )

        for key, remove_values in removals.items():
            if key in forgotten or key in values:
                continue
            row = await session.scalar(
                select(ProfileMemory)
                .where(ProfileMemory.profile_key == profile_key, ProfileMemory.key == key)
                .with_for_update()
            )
            if row is None or not isinstance(row.value, list):
                continue
            remaining = [value for value in row.value if value not in remove_values]
            if remaining:
                row.value = remaining
            else:
                await session.delete(row)

        if values:
            for key, value in values.items():
                source, evidence = _field_provenance(key, provenance)
                statement = insert(ProfileMemory).values(
                    profile_key=profile_key,
                    key=key,
                    value=value,
                    source=source,
                    evidence=evidence,
                ).on_conflict_do_update(
                    constraint="uq_profile_memories_profile_key_key",
                    set_={
                        "value": value,
                        "source": source,
                        "evidence": evidence,
                        "updated_at": func.now(),
                    },
                )
                await session.execute(statement)
        await session.commit()
    return await get_preferences(profile_key)


async def reset_preferences(profile_key: str) -> None:
    """删除演示用户的全部长期偏好。"""
    async with async_session_factory() as session:
        await session.execute(
            delete(ProfileMemory).where(ProfileMemory.profile_key == profile_key)
        )
        await session.commit()
