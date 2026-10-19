"""Postgres 异步引擎与会话生命周期。

设计决策：
- SPEC 锁定 psycopg（而非 asyncpg）：SQLAlchemy 2.0 异步走
  "postgresql+psycopg://"；psycopg[binary] 自带二进制绑定，免编译；
- pool_pre_ping 复用连接前先探活，容器重启后不会拿到死连接；
- Base 定义在此供 M1 的 models.py 继承，省去单独 base.py 文件；
- init_db() 基于 Base.metadata 建表，M0 暂无模型时为空操作，M1 起生效。
"""
from collections.abc import AsyncGenerator

from sqlalchemy import text

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# psycopg 异步引擎（惰性连接，import 时不会真正连库）
engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)

# 会话工厂：expire_on_commit=False 避免提交后访问属性触发额外查询
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型统一基类，M1 的 Product/Order/User/... 在此继承。"""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：请求级会话，请求结束自动关闭。"""
    async with async_session_factory() as session:
        yield session


async def init_db() -> None:
    """建表：M0 无模型时为空操作；M1 引入 models 后 import 即注册 metadata。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all 不会为已存在的旧表补列；订单模型演进需要显式兼容迁移。
        await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_intent_id VARCHAR(64)"))
        await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_number VARCHAR(32)"))
        await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS title_snapshot VARCHAR(200) NOT NULL DEFAULT ''"))
        await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS source_url VARCHAR(512)"))
        await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS image_url_snapshot VARCHAR(512)"))
        await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS catalog_price NUMERIC(10, 2)"))
        await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipping_fee NUMERIC(10, 2)"))
        await conn.execute(text("UPDATE orders SET order_intent_id = 'legacy-' || id::text WHERE order_intent_id IS NULL"))
        await conn.execute(text("UPDATE orders SET order_number = 'LEGACY-' || id::text WHERE order_number IS NULL"))
        await conn.execute(text("ALTER TABLE orders ALTER COLUMN order_intent_id SET NOT NULL"))
        await conn.execute(text("ALTER TABLE orders ALTER COLUMN order_number SET NOT NULL"))
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_order_intent_id ON orders(order_intent_id)"))
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_order_number ON orders(order_number)"))
