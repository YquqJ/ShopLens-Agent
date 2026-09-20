"""把 5 个 MCP server 经 langchain-mcp-adapters 加载为 LangChain 工具。

设计决策：
- 每个 server 走 stdio 子进程（独立进程，天然隔离，为 M6 单源熔断打底）；
- command 用同一 venv 的 python（sys.executable），cwd 固定 backend 根，
  保证 `-m app.mcp.xxx` 可 import；
- handle_tool_errors=False：源故障时工具直接抛异常（而非吞成字符串），
  作为「源故障」的诚实信号，也便于 M6 熔断器捕获；
- overrides 传入某源 env 实现故障注入（如 SHOPLENS_FAILRATE_PDD=1）。
"""
import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

BACKEND_DIR = Path(__file__).resolve().parents[2]

# 服务器名 -> 模块路径
SERVERS = {
    "price_jd": "app.mcp.jd",
    "price_taobao": "app.mcp.taobao",
    "price_pdd": "app.mcp.pdd",
    "coupon": "app.mcp.coupon",
    "logistics": "app.mcp.logistics",
}


def build_client(
    overrides: dict[str, dict] | None = None,
    tool_name_prefix: bool = True,
) -> MultiServerMCPClient:
    """构造 MultiServerMCPClient；overrides 按 server 名覆盖 env（故障注入用）。"""
    overrides = overrides or {}
    connections: dict[str, dict] = {}
    for name, module in SERVERS.items():
        connections[name] = {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", module],
            "cwd": str(BACKEND_DIR),
            "env": overrides.get(name),  # None -> 继承默认环境
        }
    return MultiServerMCPClient(connections, tool_name_prefix=tool_name_prefix, handle_tool_errors=False)