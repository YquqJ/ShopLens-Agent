"""商品与本地订单 MCP 服务的 LangChain 适配器。"""

import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from app.core.config import settings

ZHI_DE_MAI_URL = "https://gw-openapi.zhidemai.com/ai-mcp/streamable/mcp"
BACKEND_DIR = Path(__file__).resolve().parents[2]


def order_server_connection() -> dict:
    """Run the local order FastMCP server as a persistent stdio subprocess."""
    pythonpath = os.environ.get("PYTHONPATH")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            value for value in (str(BACKEND_DIR), pythonpath) if value
        ),
    }
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "app.mcp.order_server"],
        "env": env,
        "cwd": str(BACKEND_DIR),
    }


def haina_connection() -> dict:
    return {
        "transport": "streamable_http",
        "url": ZHI_DE_MAI_URL,
        "headers": {"x-api-key": settings.ZDM_API_KEY},
    }


def server_connections() -> dict[str, dict]:
    """构造本地订单及可选值得买 MCP 连接。"""
    connections = {"orders": order_server_connection()}
    if settings.ZDM_API_KEY:
        connections["haina"] = haina_connection()
    return connections


SERVERS = server_connections()


def build_client(
    overrides: dict[str, dict] | None = None,
    tool_name_prefix: bool = True,
) -> MultiServerMCPClient:
    """构造值得买 MCP 客户端；保留 overrides 仅为兼容旧评测调用方。"""
    return MultiServerMCPClient(
        server_connections(),
        tool_name_prefix=tool_name_prefix,
        handle_tool_errors=False,
    )


class MCPToolbox:
    """一组持久 MCP 会话上的工具与关闭句柄。"""

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
    """为值得买 server 建常驻 session，并装载动态发现的工具。"""
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
        for tool in loaded:
            tools[tool.name] = tool
    return MCPToolbox(tools, stack)
