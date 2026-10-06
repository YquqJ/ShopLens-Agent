import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import InterfaceError, OperationalError

from app.db.models import ProfileMemory
from app.db.session import async_session_factory, init_db
from app.services.profile import normalize_updates
from app.services.profile import apply_updates, get_preferences, reset_preferences


@pytest.fixture
def anyio_backend() -> tuple[str, dict]:
    """psycopg async does not support Windows' default ProactorEventLoop."""
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


@pytest.fixture
async def profile_key() -> str:
    """给每例分配独立 profile key，避免碰演示用户和其他测试数据。"""
    try:
        await init_db()
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
    except (OSError, OperationalError, InterfaceError) as exc:
        pytest.skip(f"PostgreSQL unavailable for profile integration tests: {exc}")

    key = f"pytest-profile-{uuid4()}"
    try:
        yield key
    finally:
        async with async_session_factory() as session:
            await session.execute(
                ProfileMemory.__table__.delete().where(ProfileMemory.profile_key == key)
            )
            await session.commit()


def test_normalize_updates_keeps_durable_values_and_shared_provenance() -> None:
    values, forgotten, provenance = normalize_updates({
        "preferred_colors": ["黑色", "蓝色"],
        "preferred_budget_range": {"min": 200, "max": 500},
        "budget": 300,
        "source": "explicit_command",
        "evidence": "记住我喜欢黑色和蓝色",
    })

    assert values == {
        "preferred_colors": ["黑色", "蓝色"],
    }
    assert forgotten == set()
    assert provenance == {
        "source": "explicit_command",
        "evidence": "记住我喜欢黑色和蓝色",
    }


def test_normalize_updates_keeps_gender_and_size_preferences() -> None:
    values, forgotten, _ = normalize_updates({
        "preferred_genders": ["男款"],
        "preferred_sizes": ["L"],
    })
    assert values == {"preferred_genders": ["男款"], "preferred_sizes": ["L"]}
    assert forgotten == set()


@pytest.mark.anyio
async def test_profile_budget_range_is_never_persisted(profile_key: str) -> None:
    saved = await apply_updates({
        "preferred_budget_range": {"min": 200, "max": 500},
        "preferred_colors": ["黑色"],
    }, profile_key)

    assert saved == {"preferred_colors": ["黑色"]}
    async with async_session_factory() as session:
        budget_row = await session.scalar(
            select(ProfileMemory).where(
                ProfileMemory.profile_key == profile_key,
                ProfileMemory.key == "preferred_budget_range",
            )
        )
    assert budget_row is None


def test_normalize_updates_maps_per_field_provenance_and_forget() -> None:
    values, forgotten, provenance = normalize_updates({
        "preferred_styles": {
            "value": ["简约"],
            "source": "confirmed_candidate",
            "evidence": "用户确认偏好简约",
        },
        "forget": ["preferred_materials", "budget", "unknown"],
    })

    assert values == {"preferred_styles": ["简约"]}
    assert forgotten == {"preferred_materials"}
    assert provenance == {
        "preferred_styles": {
            "source": "confirmed_candidate",
            "evidence": "用户确认偏好简约",
        },
    }


def test_normalize_updates_ignores_empty_or_unknown_values() -> None:
    values, forgotten, provenance = normalize_updates({
        "preferred_colors": [],
        "preferred_materials": None,
        "scene": "通勤",
        "source": "explicit_command",
    })

    assert values == {}
    assert forgotten == set()
    assert provenance == {"source": "explicit_command", "evidence": None}


@pytest.mark.anyio
async def test_profile_first_write_and_read_omit_persistence_metadata(profile_key: str) -> None:
    saved = await apply_updates({
        "preferred_colors": ["黑色", "蓝色"],
        "budget": 300,
        "unknown_preference": "不应持久化",
        "source": "explicit_command",
        "evidence": "记住我喜欢深色",
    }, profile_key)

    assert saved == {"preferred_colors": ["黑色", "蓝色"]}
    assert await get_preferences(profile_key) == saved
    assert "source" not in saved
    assert "evidence" not in saved
    assert "budget" not in saved
    assert "unknown_preference" not in saved
    async with async_session_factory() as session:
        unexpected_rows = await session.scalars(
            select(ProfileMemory).where(
                ProfileMemory.profile_key == profile_key,
                ProfileMemory.key.in_(["budget", "unknown_preference"]),
            )
        )
        assert unexpected_rows.all() == []


@pytest.mark.anyio
async def test_profile_replacement_keeps_one_row_and_updates_provenance(profile_key: str) -> None:
    await apply_updates({
        "preferred_styles": ["简约"],
        "source": "explicit_command",
        "evidence": "第一次偏好",
    }, profile_key)
    async with async_session_factory() as session:
        first_row = await session.scalar(
            select(ProfileMemory).where(
                ProfileMemory.profile_key == profile_key,
                ProfileMemory.key == "preferred_styles",
            )
        )
        assert first_row is not None
        assert first_row.created_at is not None
        assert first_row.updated_at is not None
        first_created_at = first_row.created_at
        first_updated_at = first_row.updated_at

        # The replacement transaction must begin after the insert timestamp.
        while await session.scalar(select(func.clock_timestamp())) <= first_updated_at:
            await asyncio.sleep(0)
    saved = await apply_updates({
        "preferred_styles": ["通勤"],
        "source": "confirmed_candidate",
        "evidence": "用户确认新偏好",
    }, profile_key)

    assert saved == {"preferred_styles": ["通勤"]}
    async with async_session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(ProfileMemory).where(
                ProfileMemory.profile_key == profile_key,
                ProfileMemory.key == "preferred_styles",
            )
        )
        row = await session.scalar(
            select(ProfileMemory).where(
                ProfileMemory.profile_key == profile_key,
                ProfileMemory.key == "preferred_styles",
            )
        )
    assert count == 1
    assert row is not None
    assert row.source == "confirmed_candidate"
    assert row.evidence == "用户确认新偏好"
    assert row.created_at == first_created_at
    assert row.updated_at > first_updated_at


@pytest.mark.anyio
async def test_profile_forget_deletes_only_requested_field(profile_key: str) -> None:
    await apply_updates({
        "preferred_colors": ["黑色"],
        "preferred_materials": ["纯棉"],
    }, profile_key)
    saved = await apply_updates({"forget": "preferred_materials"}, profile_key)

    assert saved == {"preferred_colors": ["黑色"]}
    async with async_session_factory() as session:
        deleted = await session.scalar(
            select(ProfileMemory).where(
                ProfileMemory.profile_key == profile_key,
                ProfileMemory.key == "preferred_materials",
            )
        )
    assert deleted is None


@pytest.mark.anyio
async def test_profile_remove_values_preserves_other_values_and_profile_isolation(profile_key: str) -> None:
    other_profile_key = f"{profile_key}-other"
    try:
        await apply_updates({"preferred_colors": ["黑色", "白色", "深蓝"]}, profile_key)
        await apply_updates({"preferred_colors": ["卡其"]}, other_profile_key)

        saved = await apply_updates({
            "remove_values": {"preferred_colors": ["黑色", "深蓝"]},
        }, profile_key)

        assert saved == {"preferred_colors": ["白色"]}
        assert await get_preferences(other_profile_key) == {"preferred_colors": ["卡其"]}
    finally:
        await reset_preferences(other_profile_key)


@pytest.mark.anyio
async def test_profile_reset_is_isolated_by_profile_key(profile_key: str) -> None:
    other_profile_key = f"{profile_key}-other"
    try:
        await apply_updates({"preferred_colors": ["黑色"]}, profile_key)
        await apply_updates({"preferred_styles": ["简约"]}, other_profile_key)

        await reset_preferences(profile_key)

        assert await get_preferences(profile_key) == {}
        assert await get_preferences(other_profile_key) == {"preferred_styles": ["简约"]}
    finally:
        await reset_preferences(other_profile_key)


@pytest.mark.anyio
async def test_profile_concurrent_updates_keep_one_row(profile_key: str) -> None:
    colors = ["黑色", "蓝色", "灰色", "白色", "卡其"]

    # 放大 SELECT 后、INSERT 前的竞争窗口；finally 会移除测试专用数据库对象。
    async with async_session_factory() as session:
        await session.execute(text("""
            CREATE OR REPLACE FUNCTION pytest_profile_memory_insert_delay()
            RETURNS trigger AS $$
            BEGIN
                PERFORM pg_sleep(0.1);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        await session.execute(text("""
            CREATE TRIGGER pytest_profile_memory_insert_delay
            BEFORE INSERT ON profile_memories
            FOR EACH ROW EXECUTE FUNCTION pytest_profile_memory_insert_delay()
        """))
        await session.commit()
    try:
        await asyncio.gather(*(
            apply_updates({
                "preferred_colors": [color],
                "source": "explicit_command",
                "evidence": f"并发写入 {color}",
            }, profile_key)
            for color in colors
        ))
    finally:
        async with async_session_factory() as session:
            await session.execute(text(
                "DROP TRIGGER IF EXISTS pytest_profile_memory_insert_delay ON profile_memories"
            ))
            await session.execute(text(
                "DROP FUNCTION IF EXISTS pytest_profile_memory_insert_delay()"
            ))
            await session.commit()

    preferences = await get_preferences(profile_key)
    assert preferences["preferred_colors"] in [[color] for color in colors]
    async with async_session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(ProfileMemory).where(
                ProfileMemory.profile_key == profile_key,
                ProfileMemory.key == "preferred_colors",
            )
        )
    assert count == 1
