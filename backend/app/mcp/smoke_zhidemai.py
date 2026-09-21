"""值海纳真实源冒烟：langchain-mcp-adapters 加载 zhidemai 源，按商品词召回真实商品。

验收（方案 B 第一步，对应 docs/SPEC.md 新增真实源）：
- search_real_products 经 MCP 子进程（stdio）加载后调用，返回真实商品摘要（价格/商城/图片/链接）。
"""
import asyncio
import json
import sys
from pathlib import Path

# Windows 控制台默认 GBK，打印 ¥ 等符号会 UnicodeEncodeError；统一转 UTF-8 输出
sys.stdout.reconfigure(encoding="utf-8")

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from app.core.loop import install_selector_event_loop_policy

BACKEND_DIR = Path(__file__).resolve().parents[2]


async def main() -> None:
    client = MultiServerMCPClient(
        {
            "zhidemai": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "app.mcp.zhidemai"],
                "cwd": str(BACKEND_DIR),
            }
        }
    )
    async with client.session("zhidemai") as session:
        tools = await load_mcp_tools(session, server_name="zhidemai", tool_name_prefix=True)
        tool = tools[0]
        result = await tool.ainvoke({"product_query": "iphone16", "size": 3})

    # MCP 工具结果可能是 [{'type':'text','text':'<json>'}] 或 str，统一解析
    if isinstance(result, list) and result and isinstance(result[0], dict):
        result = result[0].get("text", "")
    items = json.loads(result if isinstance(result, str) else str(result))
    print(f"召回 {len(items)} 条真实商品：")
    for it in items[:3]:
        print(json.dumps(it, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    install_selector_event_loop_policy()
    asyncio.run(main())