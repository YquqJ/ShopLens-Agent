"""开发启动入口：Windows 下用 SelectorEventLoop 启动 uvicorn（psycopg 异步需要）。

uvicorn 0.x 在 Windows 默认用 ProactorEventLoop 作 loop factory，psycopg 异步不支持；
故此处先设 selector policy，再以 loop="none" 让 uvicorn 走默认 policy 建 selector 循环。

运行：uv run python -m app.run
"""
import os

import uvicorn

from app.core.loop import install_selector_event_loop_policy


def main() -> None:
    port = int(os.environ.get("SHOPLENS_PORT", "8000"))
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, loop="none")


# 必须在 uvicorn.run 之前设置，否则循环已用 Proactor 建好
install_selector_event_loop_policy()


if __name__ == "__main__":
    main()