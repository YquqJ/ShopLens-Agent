"""Langfuse 可选接入：复用应用 OTel provider，按请求关联 Agent trace。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from langfuse import Langfuse, propagate_attributes
from langfuse.langchain import CallbackHandler
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from app.core.config import settings


def setup_langfuse(settings_obj: Any = settings, *, tracer_provider: TracerProvider | None = None):
    """按配置初始化 Langfuse，并挂到现有 OTel provider 上。"""
    if not settings_obj.LANGFUSE_ENABLED:
        return None
    if not settings_obj.LANGFUSE_PUBLIC_KEY or not settings_obj.LANGFUSE_SECRET_KEY:
        return None

    provider = tracer_provider
    if provider is None:
        current = trace.get_tracer_provider()
        provider = current if isinstance(current, TracerProvider) else None

    return Langfuse(
        public_key=settings_obj.LANGFUSE_PUBLIC_KEY,
        secret_key=settings_obj.LANGFUSE_SECRET_KEY,
        base_url=settings_obj.LANGFUSE_BASE_URL,
        environment=settings_obj.LANGFUSE_TRACING_ENVIRONMENT,
        release=settings_obj.LANGFUSE_RELEASE or None,
        sample_rate=settings_obj.LANGFUSE_SAMPLE_RATE,
        tracer_provider=provider,
    )


def create_callback_handler(client: Langfuse | None) -> CallbackHandler | None:
    """为一次 graph 调用创建 callback；未启用 Langfuse 时保持纯本地运行。"""
    return CallbackHandler() if client is not None else None


def with_langfuse_config(
    base_config: dict,
    handler: CallbackHandler | None,
    *,
    run_name: str,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> dict:
    """复制 LangGraph config，附加 callback 与请求元数据，不修改调用方对象。"""
    result = dict(base_config)
    if handler is not None:
        result["callbacks"] = [handler]
    result["run_name"] = run_name
    if metadata:
        result["metadata"] = {**result.get("metadata", {}), **metadata}
    if tags:
        result["tags"] = [*result.get("tags", []), *tags]
    return result


@contextmanager
def langfuse_request_context(
    client: Langfuse | None,
    *,
    trace_name: str,
    session_id: str,
    user_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[None]:
    """把 SSE 请求的会话和用户上下文传播到 OTel/Langfuse span。"""
    if client is None:
        yield
        return
    with propagate_attributes(
        trace_name=trace_name,
        session_id=session_id,
        user_id=user_id,
        metadata=metadata,
    ):
        yield


def shutdown_langfuse(client: Langfuse | None) -> None:
    """应用退出时刷出批量 span；未启用时无操作。"""
    if client is None:
        return
    client.flush()
    client.shutdown()
