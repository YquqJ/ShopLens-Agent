"""M4 验收驱动：对话 → 推荐 → interrupt 挂起 → 确认 → 二次询价 → 落库。

验收对照（docs/SPEC.md M4）：
- 全链路：对话 → 草稿订单 → interrupt 挂起 → 确认 → 二次询价锁价 → 下单落库。
"""
import asyncio
import json

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from app.agents.graph import build_graph
from app.agents.state import AgentState
from app.core.config import settings
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
        "lock_attempts": 0,
        "price_raised": False,
        "hop_count": 0,
    }


async def main() -> None:
    async with AsyncPostgresSaver.from_conn_string(settings.postgres_dsn) as saver:
        await saver.setup()
        graph = build_graph(checkpointer=saver)
        client = adapter.build_client()
        mcp_tools = {t.name: t for t in await client.get_tools()}

        cfg = {"configurable": {"thread_id": "m4-demo", "mcp_tools": mcp_tools}}

        # ① 跑到 interrupt 挂起
        await graph.ainvoke(_initial_state(), cfg)
        snapshot = await graph.aget_state(cfg)
        intr = snapshot.interrupts[0].value
        print("=== 推荐 ===")
        print(json.dumps(snapshot.values["recommendation"], ensure_ascii=False, indent=2))
        print("\n=== interrupt 草稿订单 ===")
        print(json.dumps(intr["draft_order"], ensure_ascii=False, indent=2))
        assert snapshot.values["recommendation"]["items"], "未产出可溯源推荐"

        # ② 确认 → 二次询价 → 落库
        final = await graph.ainvoke(Command(resume={"confirm": True}), cfg)
        order = final.get("order")
        print("\n=== 落库订单 ===")
        print(json.dumps(order, ensure_ascii=False, indent=2))
        assert order and order.get("status") == "placed", "订单未落库"
        print("\nPASS：全链路 对话→推荐→interrupt→确认→锁价→落库 跑通")


if __name__ == "__main__":
    install_selector_event_loop_policy()
    asyncio.run(main())