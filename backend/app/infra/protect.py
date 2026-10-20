"""工具保护包装：给每个 MCP 工具套「熔断 + 重试 + 观测」，失败升格为 SourceError。

设计决策：
- 在 MCP 工具外层统一包装（而非散落在各节点），源状态全局共享，节点只做编排；
- source 从工具名和描述解析，为值得买商品检索、知识图谱等工作负载隔离熔断；
- 熔断打开 → 直接抛 SourceError（不触发子进程）；重试耗尽 → record_failure + SourceError；
- SourceRegistry 持有每源 breaker、重试配置、错误预算，protect() 产出一份 name→工具 映射。
"""
from app.infra.breaker import CircuitBreaker
from app.infra.observability import ErrorBudget, trace_call
from app.infra.retry import call_with_retry


class SourceError(Exception):
    """某 MCP 源最终失败（熔断打开或重试耗尽）的信号，节点据此降级而非中断。"""

    def __init__(self, source: str, reason: str):
        self.source = source
        self.reason = reason
        super().__init__(f"[{source}] {reason}")


def source_of(tool_name: str, description: str = "") -> str:
    """Map tools to isolated source breakers, including separate Haina workloads."""
    lowered = f"{tool_name} {description}".lower()
    if "haina" in lowered or "zhidemai" in lowered:
        if any(marker in lowered for marker in (
            "knowledge graph", "knowledge_graph", "knowledge-graph", "entity", "实体", "知识图谱", " kg ", "_kg_",
        )):
            return "haina_kg"
        if any(marker in lowered for marker in ("product", "商品", "产品")):
            return "haina_product"
        return "haina_unknown"
    if tool_name.startswith("price_"):
        return tool_name.split("_")[1]
    return tool_name.split("_")[0]


class ProtectedTool:
    def __init__(self, inner, source: str, registry: "SourceRegistry"):
        self._inner = inner
        self.source = source
        self._registry = registry
        self.name = getattr(inner, "name", None)
        self.description = getattr(inner, "description", "")
        self.args = getattr(inner, "args", {})
        self.args_schema = getattr(inner, "args_schema", None)

    async def ainvoke(self, *args, **kwargs):
        self._registry.record_logical_call(self.source)
        breaker = self._registry.breaker(self.source)
        if not breaker.allow():
            self._registry.budget.on_skipped(self.source)
            self._registry.record_circuit_skip(self.source)
            raise SourceError(self.source, "熔断打开，已降级跳过")
        attempt_count = 0
        backoff_seconds = 0.0

        async def invoke_inner():
            nonlocal attempt_count
            attempt_count += 1
            return await self._inner.ainvoke(*args, **kwargs)

        def record_retry(_attempt: int, sleep_seconds: float) -> None:
            nonlocal backoff_seconds
            backoff_seconds += sleep_seconds

        async with trace_call(self._registry.tracer, self.source, f"mcp.{self.name}"):
            try:
                result = await call_with_retry(
                    invoke_inner,
                    on_retry=record_retry,
                    **self._registry.retry_kwargs,
                )
            except Exception as e:  # noqa: BLE001
                breaker.record_failure()
                self._registry.budget.on_failure(self.source)
                self._registry.record_tool_result(
                    self.source, attempts=attempt_count, backoff_seconds=backoff_seconds, succeeded=False,
                )
                raise SourceError(self.source, f"{type(e).__name__}: {e}") from e
        breaker.record_success()
        self._registry.budget.on_success(self.source)
        self._registry.record_tool_result(
            self.source, attempts=attempt_count, backoff_seconds=backoff_seconds, succeeded=True,
        )
        return result


class SourceRegistry:
    """每源熔断器 + 重试配置 + 错误预算的持有者（app 生命周期内共享）。"""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 5.0,
        retry_kwargs: dict | None = None,
        slo: float = 0.99,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.retry_kwargs = retry_kwargs or {
            "attempts": 2,
            "base_seconds": 0.05,
            "max_seconds": 0.5,
        }
        self.budget = ErrorBudget(slo=slo)
        self.tracer = None  # 由 app 入口注入 OTel tracer，可选
        self._breakers: dict[str, CircuitBreaker] = {}
        self._tool_metrics: dict[str, dict[str, float | int]] = {}

    def breaker(self, source: str) -> CircuitBreaker:
        if source not in self._breakers:
            self._breakers[source] = CircuitBreaker(
                self.failure_threshold, self.cooldown_seconds
            )
        return self._breakers[source]

    def breaker_states(self) -> dict[str, str]:
        """每个已建源 → 当前状态（closed/open/half_open），供调试面板展示三态。"""
        return {src: b.state.value for src, b in self._breakers.items()}

    def protect(self, tools: dict) -> dict:
        """给一批 name→工具 套保护包装，返回 name→ProtectedTool。"""
        return {
            name: ProtectedTool(tool, source_of(name, getattr(tool, "description", "")), self)
            for name, tool in tools.items()
        }

    def report(self) -> dict:
        return self.budget.report()

    def record_logical_call(self, source: str) -> None:
        self._metrics(source)["logical_calls"] += 1

    def record_circuit_skip(self, source: str) -> None:
        self._metrics(source)["circuit_open_skips"] += 1

    def record_tool_result(
        self, source: str, *, attempts: int, backoff_seconds: float, succeeded: bool,
    ) -> None:
        metrics = self._metrics(source)
        metrics["attempts"] += attempts
        metrics["retries"] += max(0, attempts - 1)
        metrics["backoff_scheduled_seconds"] += backoff_seconds
        if attempts > 1:
            key = "recovered_calls" if succeeded else "retry_failed_calls"
            metrics[key] += 1

    def tool_metrics_report(self) -> dict:
        sources = set(self._tool_metrics) | set(self._breakers)
        report = {}
        for source in sources:
            metrics = dict(self._metrics(source))
            retried_calls = metrics["recovered_calls"] + metrics["retry_failed_calls"]
            metrics["retry_recovery_rate"] = (
                metrics["recovered_calls"] / retried_calls if retried_calls else None
            )
            metrics["breaker_state"] = self.breaker(source).state.value
            metrics["breaker_transitions"] = dict(self.breaker(source).transition_counts)
            report[source] = metrics
        return report

    def _metrics(self, source: str) -> dict[str, float | int]:
        if source not in self._tool_metrics:
            self._tool_metrics[source] = {
                "logical_calls": 0,
                "attempts": 0,
                "retries": 0,
                "recovered_calls": 0,
                "retry_failed_calls": 0,
                "circuit_open_skips": 0,
                "backoff_scheduled_seconds": 0.0,
            }
        return self._tool_metrics[source]
