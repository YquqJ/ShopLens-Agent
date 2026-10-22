"""用于测试 MCP 保护层的确定性故障注入工具。

该模块只在测试和实验脚本中显式使用，不参与正式 MCP 连接装配。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class HttpFault(Exception):
    """带 HTTP 状态码的模拟下游错误。"""

    def __init__(self, status_code: int, message: str | None = None):
        self.status_code = status_code
        super().__init__(message or f"simulated HTTP {status_code}")


@dataclass(frozen=True)
class Fault:
    """一次可注入的故障。"""

    kind: str
    message: str = "simulated MCP fault"
    status_code: int | None = None

    @classmethod
    def network(cls, message: str = "simulated network failure") -> "Fault":
        return cls("network", message)

    @classmethod
    def timeout(cls, message: str = "simulated timeout") -> "Fault":
        return cls("timeout", message)

    @classmethod
    def rate_limit(cls, message: str = "simulated rate limit") -> "Fault":
        return cls("http", message, 429)

    @classmethod
    def server_error(cls, status_code: int = 503, message: str | None = None) -> "Fault":
        if not 500 <= status_code < 600:
            raise ValueError("server_error status_code must be 5xx")
        return cls("http", message or f"simulated HTTP {status_code}", status_code)

    @classmethod
    def invalid_request(cls, message: str = "simulated invalid MCP arguments") -> "Fault":
        return cls("invalid", message)

    def exception(self) -> BaseException:
        if self.kind == "network":
            return ConnectionError(self.message)
        if self.kind == "timeout":
            return TimeoutError(self.message)
        if self.kind == "http":
            return HttpFault(self.status_code or 500, self.message)
        if self.kind == "invalid":
            return ValueError(self.message)
        raise ValueError(f"unknown fault kind: {self.kind}")


class FaultInjectedTool:
    """按调用顺序注入故障的 MCP Tool 替身。"""

    description = "Fault-injected MCP product search tool"

    def __init__(
        self,
        name: str,
        faults: list[Fault | None],
        *,
        result: Any = None,
    ):
        self.name = name
        self._faults = list(faults)
        self.result = result
        self.calls = 0

    async def ainvoke(self, *_args, **_kwargs):
        self.calls += 1
        fault = self._faults.pop(0) if self._faults else None
        if fault is not None:
            raise fault.exception()
        return self.result
