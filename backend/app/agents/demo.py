"""M3 验收驱动：文字需求跑通「澄清 → 推荐」全链路。

验收对照（docs/SPEC.md M3）：
- 走完 澄清 → 推荐，产出 ≥1 条可溯源推荐；
- 同一商品三源报价归一为一条（normalized 每项 quotes 长度 = 3）。
"""
import asyncio
import json

from langchain_core.messages import HumanMessage

from app.agents.graph import build_graph
from app.agents.state import AgentState
from app.core.loop import install_selector_event_loop_policy
from app.mcp import adapter

QUERY = "给女朋友买项链，预算2000，喜欢简约"


def _initial_state() -> AgentState:
    return {
        "messages": [HumanMessage(QUERY)],
        "preferences": {},
        "intent": "",
        "needs_action": "",
        "candidates": [],
        "quotes": [],
        "normalized": [],
        "recommendation": {},
        "draft_order": None,
        "order": None,
        "hop_count": 0,
    }


async def main() -> None:
    graph = build_graph()
    # 加载 5 个 MCP 工具（比价节点用），与图共享同一事件循环会话
    client = adapter.build_client()
    mcp_tools = {t.name: t for t in await client.get_tools()}

    result = await graph.ainvoke(
        _initial_state(), config={"configurable": {"mcp_tools": mcp_tools}}
    )

    print("=== 偏好 ===")
    print(json.dumps(result["preferences"], ensure_ascii=False))
    print("\n=== 归一（三源报价合并为一条）===")
    print(json.dumps(result["normalized"], ensure_ascii=False, indent=2))
    print("\n=== 推荐 ===")
    print(json.dumps(result["recommendation"], ensure_ascii=False, indent=2))

    # 验收断言
    assert result["recommendation"].get("items"), "未产出可溯源推荐"
    assert any(len(i["quotes"]) == 3 for i in result["normalized"]), "三源报价未归一为一条"
    print("\nPASS：产出", len(result["recommendation"]["items"]), "条可溯源推荐，三源报价正确归一")


if __name__ == "__main__":
    install_selector_event_loop_policy()
    asyncio.run(main())