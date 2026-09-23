import asyncio
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError

from app.api import profile as profile_api
from app.db.models import Conversation, User
from app.db.session import async_session_factory, init_db
from app.services import history
from app.services.order import ensure_conversation


class SnapshotGraph:
    async def aget_state(self, _config: dict) -> SimpleNamespace:
        return SimpleNamespace(
            values={
                "messages": [],
                "profile_preferences": {"preferred_colors": ["旧快照"]},
            },
            interrupts=[],
        )


@pytest.mark.anyio
async def test_history_detail_uses_current_browser_profile_over_thread_snapshot(monkeypatch) -> None:
    get_preferences = AsyncMock(return_value={"preferred_colors": ["黑色"]})
    require_owner = AsyncMock(return_value=1)
    monkeypatch.setattr(history.profile, "get_preferences", get_preferences)
    monkeypatch.setattr(history, "require_conversation_owner", require_owner)

    detail = await history.get_conversation(
        SnapshotGraph(),
        "thread-1",
        profile_id="browser-profile-1",
    )

    require_owner.assert_awaited_once_with("thread-1", "browser-profile-1")
    get_preferences.assert_awaited_once_with("browser-profile-1")
    assert detail["profile_preferences"] == {"preferred_colors": ["黑色"]}


@pytest.mark.anyio
async def test_history_detail_hides_thread_from_a_mismatched_browser_profile(monkeypatch) -> None:
    require_owner = AsyncMock(side_effect=PermissionError("owner mismatch"))
    monkeypatch.setattr(history, "require_conversation_owner", require_owner)

    detail = await history.get_conversation(
        SnapshotGraph(),
        "thread-owned-by-another-browser",
        profile_id="browser-profile-2",
    )

    require_owner.assert_awaited_once_with(
        "thread-owned-by-another-browser",
        "browser-profile-2",
    )
    assert detail is None


@pytest.fixture
def anyio_backend() -> tuple[str, dict]:
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


@pytest.fixture
async def browser_profile() -> tuple[str, str]:
    try:
        await init_db()
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
    except (OSError, OperationalError, InterfaceError) as exc:
        pytest.skip(f"PostgreSQL unavailable for conversation ownership tests: {exc}")

    owner = f"pytest-owner-{uuid4()}"
    thread_id = f"pytest-thread-{uuid4()}"
    try:
        yield owner, thread_id
    finally:
        async with async_session_factory() as session:
            result = await session.execute(
                text("""
                    DELETE FROM conversations
                    WHERE thread_id = :thread_id
                    RETURNING user_id
                """),
                {"thread_id": thread_id},
            )
            user_id = result.scalar_one_or_none()
            if user_id is not None:
                await session.execute(User.__table__.delete().where(User.id == user_id))
            await session.commit()


@pytest.mark.anyio
async def test_conversation_owner_cannot_reuse_another_profile_thread(browser_profile: tuple[str, str]) -> None:
    owner, thread_id = browser_profile
    await ensure_conversation(thread_id, owner)

    with pytest.raises(PermissionError):
        await ensure_conversation(thread_id, f"other-{uuid4()}")

    async with async_session_factory() as session:
        conversation = await session.scalar(
            Conversation.__table__.select().where(Conversation.thread_id == thread_id)
        )
    assert conversation is not None


@pytest.mark.anyio
async def test_profile_preferences_endpoint_reads_without_any_conversation(monkeypatch) -> None:
    get_preferences = AsyncMock(return_value={"preferred_styles": ["简约"]})
    monkeypatch.setattr(profile_api.profile, "get_preferences", get_preferences)

    response = await profile_api.get_profile_preferences("browser-profile-without-history")

    get_preferences.assert_awaited_once_with("browser-profile-without-history")
    assert response == {"profile_preferences": {"preferred_styles": ["简约"]}}
