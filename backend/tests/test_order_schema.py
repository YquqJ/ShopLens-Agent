from pathlib import Path

import pytest
from sqlalchemy import Text

from app.db.models import Order
from app.db import session as db_session


def test_order_source_url_uses_unbounded_text_column():
    assert isinstance(Order.__table__.c.source_url.type, Text)


def test_order_source_url_migration_converts_existing_column_to_text():
    migration = Path(__file__).parents[1] / "migrations" / "003_order_source_url_text.sql"

    assert migration.exists()
    sql = migration.read_text(encoding="utf-8").lower()
    assert "alter table orders alter column source_url type text" in sql


@pytest.mark.anyio
async def test_init_db_alters_existing_source_url_column_to_text(monkeypatch):
    statements = []

    class Connection:
        async def run_sync(self, callback):
            callback(self)

        async def execute(self, statement):
            statements.append(str(statement).lower())

    class Engine:
        def begin(self):
            return self

        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(db_session, "engine", Engine())
    monkeypatch.setattr(db_session.Base.metadata, "create_all", lambda _connection: None)

    await db_session.init_db()

    assert any("alter table orders alter column source_url type text" in sql for sql in statements)
