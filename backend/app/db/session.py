"""Postgres 异步引擎与会话生命周期。

设计决策：
- SPEC 锁定 psycopg（而非 asyncpg）：SQLAlchemy 2.0 异步走
  "postgresql+psycopg://"；psycopg[binary] 自带二进制绑定，免编译；
- pool_pre_ping 复用连接前先探活，容器重启后不会拿到死连接；
- Base 定义在此供 M1 的 models.py 继承，省去单独 base.py 文件；
- init_db() 基于 Base.metadata 建表，M0 暂无模型时为空操作，M1 起生效。
"""
from collections.abc import AsyncGenerator

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