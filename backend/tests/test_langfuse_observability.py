from contextlib import contextmanager

import pytest
from pydantic import BaseModel
from opentelemetry.sdk.trace import TracerProvider

import app.infra.langfuse as lf
import app.agents.llm as llm_module


def test_disabled_langfuse_does_not_construct_client(monkeypatch):
    class Disabled:
        LANGFUSE_ENABLED = False

    def fail_constructor(**_kwargs):
        raise AssertionError("Langfuse must not be constructed when disabled")

    monkeypatch.setattr(lf, "Langfuse", fail_constructor)

    assert lf.setup_langfuse(Disabled()) is None


def test_enabled_langfuse_uses_existing_tracer_provider(monkeypatch):
    captured = {}

    class Enabled:
        LANGFUSE_ENABLED = True
        LANGFUSE_PUBLIC_KEY = "public"
        LANGFUSE_SECRET_KEY = "secret"
        LANGFUSE_BASE_URL = "http://langfuse.local"
        LANGFUSE_TRACING_ENVIRONMENT = "test"
        LANGFUSE_RELEASE = "build-1"
        LANGFUSE_SAMPLE_RATE = 0.5

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    provider = TracerProvider()
    monkeypatch.setattr(lf, "Langfuse", FakeClient)

    client = lf.setup_langfuse(Enabled(), tracer_provider=provider)

    assert isinstance(client, FakeClient)
    assert captured["tracer_provider"] is provider
    assert captured["sample_rate"] == 0.5
    assert captured["environment"] == "test"


def test_run_config_adds_callback_and_metadata_without_mutating_base():
    base = {"configurable": {"thread_id": "thread-1"}}
    handler = object()

    result = lf.with_langfuse_config(
        base,
        handler,
        run_name="shoplens.chat",
        metadata={"thread_id": "thread-1", "profile_id": "profile-1"},
    )

    assert base == {"configurable": {"thread_id": "thread-1"}}
    assert result["callbacks"] == [handler]
    assert result["run_name"] == "shoplens.chat"
    assert result["metadata"]["profile_id"] == "profile-1"


def test_request_context_propagates_langfuse_attributes(monkeypatch):
    calls = {}

    @contextmanager
    def fake_propagate_attributes(**kwargs):
        calls.update(kwargs)
        yield

    monkeypatch.setattr(lf, "propagate_attributes", fake_propagate_attributes)

    with lf.langfuse_request_context(
        object(),
        trace_name="shoplens.chat",
        session_id="thread-1",
        user_id="profile-1",
        metadata={"channel": "sse"},
    ):
        pass

    assert calls == {
        "trace_name": "shoplens.chat",
        "session_id": "thread-1",
        "user_id": "profile-1",
        "metadata": {"channel": "sse"},
    }


@pytest.mark.anyio
async def test_structured_llm_forwards_config_to_inner_llm(monkeypatch):
    class Result(BaseModel):
        value: str

    class FakeResponse:
        content = '{"value": "ok"}'

    class FakeLLM:
        def __init__(self):
            self.kwargs = None

        async def ainvoke(self, _messages, **kwargs):
            self.kwargs = kwargs
            return FakeResponse()

    fake = FakeLLM()
    monkeypatch.setattr(llm_module, "get_llm", lambda _model: fake)

    runnable = llm_module.get_structured_llm("test-model", Result)
    config = {"callbacks": [object()]}
    result = await runnable.ainvoke([], config=config)

    assert result.value == "ok"
    assert fake.kwargs["config"] is config
