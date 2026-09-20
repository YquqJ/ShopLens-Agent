"""评测脚本共享工具：事件循环策略 + MCP 工具装配。

judge.py / clarify.py 不依赖 MCP；token_profiler.py / chaos.py 需要 MCP 工具与 selector 循环，
故抽到此处复用，避免每个脚本重复装配逻辑。
"""
import os

from app.core.loop import install_selector_event_loop_policy
from app.mcp import adapter


def apply_loop_policy() -> None:
    """Windows 下切 selector 循环（MCP 子进程 async 需要）。须在事件循环创建前调用。"""
    install_selector_event_loop_policy()


async def build_mcp_tools(overrides: dict[str, dict] | None = None) -> dict[str, object]:
    """装配 MCP 工具（name -> tool）；overrides 按 server 名覆盖 env 实现故障注入。

    overrides 的 env 与 os.environ 合并后传给子进程：langchain-mcp-adapters 的 stdio env
    在各 SDK 版本间存在「合并 / 整包替换」差异，合并写死保证子进程不丢 PATH/PYTHONPATH。
    """
    merged: dict[str, dict] = {}
    for name, extra in (overrides or {}).items():
        merged[name] = {**os.environ, **(extra or {})}
    client = adapter.build_client(overrides=merged or None)
    return {t.name: t for t in await client.get_tools()}