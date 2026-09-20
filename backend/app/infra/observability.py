"""观测：OpenTelemetry 插桩（trace）+ 进程内错误预算看板。

设计决策：
- OTel 只做 trace span（默认 ConsoleSpanExporter，无 collector 也能在日志里看到 span），
  业务指标（错误预算）用进程内 ErrorBudget 计数器记录——轻量、本地可查、面试可讲清；
- 错误预算按 SRE 语义：SLO=99% 目标成功率，允许失败 = (1-SLO)×总调用，
  剩余预算 < 0 即「突破 SLO」。report() 供 /metrics 或日志看板消费。
"""
from contextlib import asynccontextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor


def setup_tracer(service_name: str = "shoplens") -> trace.Tracer:
    """幂等建立 TracerProvider（ConsoleExporter），返回 tracer。"""
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


class ErrorBudget:
    """每源成功/失败/熔断跳过计数，算出错误率与剩余错误预算。"""

    def __init__(self, slo: float = 0.99):
        self.slo = slo
        self._d: dict[str, dict] = {}

    def _src(self, source: str) -> dict:
        return self._d.setdefault(source, {"success": 0, "failure": 0, "skipped": 0})

    def on_success(self, source: str) -> None:
        self._src(source)["success"] += 1

    def on_failure(self, source: str) -> None:
        self._src(source)["failure"] += 1

    def on_skipped(self, source: str) -> None:
        self._src(source)["skipped"] += 1

    def error_rate(self, source: str) -> float:
        s = self._src(source)
        attempts = s["success"] + s["failure"]
        return s["failure"] / attempts if attempts else 0.0

    def budget_remaining(self, source: str) -> float:
        """剩余错误预算（含熔断跳过计为一次「不成功」）。可为负 = 突破 SLO。"""
        s = self._src(source)
        total = s["success"] + s["failure"] + s["skipped"]
        return (1 - self.slo) * total - s["failure"]

    def report(self) -> dict:
        out = {}
        for src, s in self._d.items():
            out[src] = {
                "success": s["success"],
                "failure": s["failure"],
                "skipped": s["skipped"],
                "error_rate": round(self.error_rate(src), 4),
                "error_budget_remaining": round(self.budget_remaining(src), 4),
            }
        return out


@asynccontextmanager
async def trace_call(tracer: Any, source: str, name: str):
    """给一次工具调用挂 span（tracer 为 None 时无操作，纯透传）。"""
    if tracer is None:
        yield
        return
    with tracer.start_as_current_span(name, attributes={"source": source}) as span:
        try:
            yield
        except Exception as e:  # noqa: BLE001
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR))
            raise