"""ORM 模型：Product / User / Conversation / Order / ConversionEvent。

设计决策：
- Product 是商品主数据（价格/库存/属性），Postgres 是唯一事实来源；
  Chroma 只存「向量 + 最小元数据」，检索命中后用 sku_id 回 Postgres 取权威数据；
- 金额用 Numeric(10,2) 固化落库，杜绝 float 精度问题与 LLM 现编；
- User/Conversation/Order/ConversionEvent 为 M4 下单闭环与 M8 转化漏斗预留，
  M1 先落表结构（init_db 建表生效），字段随对应里程碑扩充。
"""
from datetime import datetime
from decimal import Decimal
import secrets
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def _default_order_number() -> str:
    return datetime.now().strftime("%Y%m%d%H%M") + f"{secrets.randbelow(1000):03d}"


class Product(Base):
    """商品主数据：价格/库存/属性的权威来源。"""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32), index=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    stock: Mapped[int] = mapped_column(Integer, default=0)
    image_url: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class User(Base):
    """用户（会话归因到人，含外部标识占位）。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Conversation(Base):
    """会话：thread_id 对应 LangGraph checkpointer 的 thread_id，用于续聊/断点恢复。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileMemory(Base):
    """单个用户画像字段的长期偏好及其写入来源。"""

    __tablename__ = "profile_memories"
    __table_args__ = (
        UniqueConstraint("profile_key", "key", name="uq_profile_memories_profile_key_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_key: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[object] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(64), default="explicit_command")
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Order(Base):
    """订单：确认后的商品快照与意图幂等键固化落库。"""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_intent_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=lambda: str(uuid4())
    )
    order_number: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, default=_default_order_number
    )
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sku_id: Mapped[str] = mapped_column(String(64))
    title_snapshot: Mapped[str] = mapped_column(String(200), default="")
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    image_url_snapshot: Mapped[str | None] = mapped_column(String(512), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(16), nullable=True)  # jd/taobao/pdd/主库
    catalog_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    quoted_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    shipping_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    final_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # 是否购买首推（👍推荐品）
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConversionEvent(Base):
    """转化事件（曝光·点击·下单），M8 转化漏斗归因用。"""

    __tablename__ = "conversion_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), index=True, nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(16), index=True)  # impression/click/order
    sku_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
