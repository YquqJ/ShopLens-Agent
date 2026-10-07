"""分级重试：MCP 源临时故障重试+退避；不可重试动作（下单）不重试。

设计决策：
- 用 tenacity 统一退避（指数+抖动）；MCP 比价源的下游故障视为「可重试」（瞬时抖动），
  由 call_with_retry 做 attempts 次尝试后仍失败才上抛；
- 下单（place_order）不可重试：扣款/落库类动作不做静默重试，失败交 HITL 人工或显式报错，
  故本模块只服务「可重试」的 MCP 源调用，不可重试路径显式 attempts=1 或根本不套本函数。
"""
from collections.abc import Callable

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

# 默认可重试 = 任意异常（MCP 源下游瞬时故障统一按可重试处理）
_DEFAULT_RETRYABLE = retry_if_exception_type(Exception)


async def call_with_retry(
    fn,
    *,
    attempts: int = 2,
    base_seconds: float = 0.05,
    max_seconds: float = 0.5,
    on_retry: Callable[[int, float], None] | None = None,
) -> object:
    """对可重试动作 fn 做 attempts 次尝试（指数退避+抖动），最终失败上抛。

    attempts 语义 = 总尝试次数（含第一次），stop_after_attempt(N) 即 N 次。
    """
    def before_sleep(retry_state) -> None:
        if on_retry is not None:
            sleep_seconds = retry_state.next_action.sleep if retry_state.next_action else 0.0
            on_retry(retry_state.attempt_number, sleep_seconds)

    retryer = AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(initial=base_seconds, max=max_seconds),
        retry=_DEFAULT_RETRYABLE,
        reraise=True,
        before_sleep=before_sleep,
    )
    async for attempt in retryer:
        with attempt:
            return await fn()
