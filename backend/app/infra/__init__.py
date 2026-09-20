"""可靠性基础设施：熔断器 / 重试 / 降级 / 观测（M6）。

- breaker.py       三态熔断器（CLOSED→OPEN→HALF_OPEN）
- retry.py         tenacity 分级重试（可重试 vs 不可重试）
- protect.py       MCP 工具保护包装（熔断+重试+观测），失败升格 SourceError
- degrade.py       降级策略（单源标注 / 全源退主库价）
- observability.py OpenTelemetry trace + 进程内错误预算看板
"""