"""MCP 比价数据源（★差异化）：jd/taobao/pdd 三比价源 + coupon/logistics。

每个源是独立 FastMCP server（stdio 子进程），经 langchain-mcp-adapters 热插拔为工具。
"""