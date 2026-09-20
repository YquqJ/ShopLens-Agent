"""Windows 事件循环策略：psycopg 异步须用 selector 循环（详见 docs/DESIGN_NOTES.md）。

须在事件循环创建之前调用。所有入口（seed 脚本 / main.py / eval 脚本 / pytest）
顶部统一调用，避免散落重复的 asyncio.set_event_loop_policy。
"""
import asyncio
import sys


def install_selector_event_loop_policy() -> None:
    """Windows 下将事件循环切为 selector 循环，兼容 psycopg 异步模式。"""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())