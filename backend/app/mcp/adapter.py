"""把 6 个 MCP server 经 langchain-mcp-adapters 加载为 LangChain 工具。

设计决策：
- 每个 server 走 stdio 子进程（独立进程，天然隔离，为 M6 单源熔断打底）；
- command 用同一 venv 的 python（sys.executable），cwd 固定 backend 根，
  保证 `-m app.mcp.xxx` 可 import；
- handle_tool_errors=False：源故障时工具直接抛异常（而非吞成字符串），
  作为「源故障」的诚实信号，也便于 M6 熔断器捕获；
- overrides 传入某源 env 实现故障注入（如 SHOPLENS_FAILRATE_PDD=1）。
"""
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

BACKEND_DIR = Path(__file__).resolve().parents[2]

# 服务器名 -> 模块路径
SERVERS = {
    "price_jd": "app.mcp.jd",
    "price_taobao": "app.mcp.taobao",
    "price_pdd": "app.mcp.pdd",
    "coupon": "app.mcp.coupon",
    "logistics": "app.mcp.logistics",
    "zhidemai": "app.mcp.zhidemai",
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


class MCPToolbox:
    """一组「持久会话」上的 MCP 工具 + 会话关闭句柄。

    get_tools() 返回的工具每次调用都会重开 session（stdio 下=每次 spawn 子进程 + MCP 握手，
    单次 ~500ms）；改用 client.session(name) 常驻子进程，工具走会话 RPC 复用，单次降到毫秒级。
    """

    def __init__(self, tools: dict, stack: AsyncExitStack):
        self.tools = tools
        self._stack = stack

    async def __aenter__(self) -> "MCPToolbox":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def aclose(self) -> None:
        await self._stack.aclose()


async def build_persistent_toolbox(
    client: MultiServerMCPClient | None = None,
    *,
    overrides: dict[str, dict] | None = None,
    tool_name_prefix: bool = True,
) -> MCPToolbox:
    """为每个 server 建常驻 session，把工具装载到 session 上（复用连接，不重复握手）。

    overrides 同 build_client（按 server 名覆盖 env，故障注入）；handle_tool_errors=False
    保证源故障以异常上抛（供 infra/protect.py 的熔断器捕获），而非吞成字符串。
    """
    client = client or build_client(overrides=overrides, tool_name_prefix=tool_name_prefix)
    stack = AsyncExitStack()
    tools: dict = {}
    for name in SERVERS:
        session = await stack.enter_async_context(client.session(name))
        loaded = await load_mcp_tools(
            session,
            server_name=name,
            tool_name_prefix=tool_name_prefix,
            handle_tool_errors=False,
        )
        for t in loaded:
            tools[t.name] = t
    return MCPToolbox(tools, stack)
