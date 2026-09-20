"""三态熔断器（手写）：CLOSED → OPEN → HALF_OPEN。

设计决策：
- 连续失败达阈值 → OPEN，冷却期内一律快速失败（不触发下游）；冷却结束放一个探测 → HALF_OPEN，
  探测成功回 CLOSED、失败重新 OPEN；
- 手写而非现成库：状态机仅三态、逻辑透明，便于面试讲清，也避免为重库引入重依赖；
- 本应用单 Agent 顺序执行，HALF_OPEN 只放一个探测（用 _probing 防并发多探测）。
"""
import time
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BrokenCircuit(Exception):
    """熔断器打开时抛出（快速失败信号，比实际下游报错更早中断）。"""


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 5.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._probing = False  # HALF_OPEN 是否已有探测在途

    @property
    def state(self) -> CircuitState:
        # OPEN 且冷却结束 → 自动转入 HALF_OPEN（惰性，下次 allow() 时才算）
        if self._state == CircuitState.OPEN and self._cooled_down():
            self._state = CircuitState.HALF_OPEN
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def allow(self) -> bool:
        """本次是否允许尝试调用。"""
        s = self.state
        if s == CircuitState.CLOSED:
            return True
        if s == CircuitState.HALF_OPEN:
            if self._probing:
                return False
            self._probing = True
            return True
        return False  # OPEN 冷却未到，快速失败

    def record_success(self) -> None:
        self._probing = False
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._probing = False
        self._consecutive_failures += 1
        if self._state == CircuitState.HALF_OPEN or self._consecutive_failures >= self.failure_threshold:
            self._open()

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()

    def _cooled_down(self) -> bool:
        return time.monotonic() - self._opened_at >= self.cooldown_seconds