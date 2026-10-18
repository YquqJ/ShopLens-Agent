"""把本地仿真 MCP 与值得买远程 MCP 加载为 LangChain 工具。

设计决策：
- 本地仿真报价/优惠券/物流 server 走 stdio 子进程（独立进程，天然隔离，为 M6 单源熔断打底）；
- 值得买真实源接入官方 Streamable HTTP MCP，API Key 通过请求头传递；
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
from app.core.config import settings

BACKEND_DIR = Path(__file__).resolve().parents[2]

# 本地仿真 server 名 -> 模块路径
LOCAL_SERVERS = {
    "price_jd": "app.mcp.jd",
    "price_taobao": "app.mcp.taobao",
    "price_pdd": "app.mcp.pdd",
    "coupon": "app.mcp.coupon",
    "logistics": "app.mcp.logistics",
}

ZHI_DE_MAI_URL = "https://gw-openapi.zhidemai.com/ai-mcp/streamable/mcp"


def haina_connection() -> dict:
    return {
        "transport": "streamable_http",
        "url": ZHI_DE_MAI_URL,
        "headers": {"x-api-key": settings.ZDM_API_KEY},
    }


def server_connections() -> dict[str, dict]:
    """构造 MCP 连接；真实源未配置密钥时不阻塞本地仿真模式启动。"""
    connections: dict[str, dict] = {
        name: {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", module],
            "cwd": str(BACKEND_DIR),
        }
        for name, module in LOCAL_SERVERS.items()
    }
    if settings.ZDM_API_KEY:
        connections["haina"] = haina_connection()
    return connections


SERVERS = server_connections()


def build_client(
    overrides: dict[str, dict] | None = None,
    tool_name_prefix: bool = True,
) -> MultiServerMCPClient:
    """构造 MultiServerMCPClient；overrides 按 server 名覆盖 env（故障注入用）。"""
    overrides = overrides or {}
    connections = server_connections()
    for name, connection in connections.items():
        if connection["transport"] == "stdio":
            connection["env"] = overrides.get(name)  # None -> 继承默认环境
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
    保证本地源故障以异常上抛（供 infra/protect.py 的熔断器捕获），而非吞成字符串。
    Haina 官方远程 MCP 连接由 Streamable HTTP 建立，不参与本地仿真源故障注入。
    """
    client = client or build_client(overrides=overrides, tool_name_prefix=tool_name_prefix)
    stack = AsyncExitStack()
    tools: dict = {}
    for name in client.connections:
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
