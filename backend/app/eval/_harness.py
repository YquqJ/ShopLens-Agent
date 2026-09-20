"""评测脚本共享工具：事件循环策略 + 受保护的 MCP 工具装配。

token_profiler.py / chaos.py 需要「熔断+重试+观测」保护下的 MCP 工具与 selector 循环，
故抽到此处复用；judge.py / clarify.py 不依赖 MCP，直接用各自逻辑。
"""
import os
from contextlib import asynccontextmanager

from app.core.loop import install_selector_event_loop_policy
from app.infra.protect import SourceRegistry
from app.mcp import adapter


def apply_loop_policy() -> None:
    """Windows 下切 selector 循环（MCP 子进程 async 需要）。须在事件循环创建前调用。"""
    install_selector_event_loop_policy()


@asynccontextmanager
async def mcp_tools_ctx(overrides: dict[str, dict] | None = None, *, registry: SourceRegistry | None = None):
    """持久会话 + 保护包装：yield (protected_tools, registry)；退出时关闭子进程会话。

    overrides 按 server 名覆盖 env 实现故障注入，env 与 os.environ 合并传给子进程，
    避免各 SDK 版本「合并/替换」差异导致子进程丢 PATH/PYTHONPATH。
    """
    merged: dict[str, dict] = {}
    for name, extra in (overrides or {}).items():
        merged[name] = {**os.environ, **(extra or {})}
    registry = registry or SourceRegistry()
    toolbox = await adapter.build_persistent_toolbox(overrides=merged or None)
    try:
        yield registry.protect(toolbox.tools), registry
    finally:
        await toolbox.aclose()