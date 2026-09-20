"""token 基线：固定会话集跑 clarify/route/recommend 三处 LLM 调用，统计 token。

设计决策：
- M5 只落地「全量(full)」基线；「裁剪(trimmed)」是 M7 的目标，`--mode trimmed` 留占位，
  避免提前实现裁剪（提前优化）。
- 计数用 tiktoken cl100k_base 近似（DashScope/Qwen 官方分词器略有出入，但作为
  「全量 vs 裁剪」的相对对比足够；输入侧由节点消息精确重建，输出侧用结构化结果的
  model_dump json 近似）。
- recommend 的上下文本该来自检索→比价→归一的真实产物，此处用固定候选 + 真实 MCP
  报价归一出快照，保证 token 大小来自真实管线；数值不依赖 Chroma/Postgres。
"""
import argparse
import asyncio
import json
from pathlib import Path

import tiktoken
from langchain_core.messages import HumanMessage

from app.agents.nodes import clarify as clarify_mod
from app.agents.nodes import recommend as recommend_mod
from app.agents.nodes import route as route_mod
from app.agents.nodes.compare import compare_node
from app.agents.nodes.normalize import normalize_node
from app.agents.state import AgentState
from app.eval._harness import apply_loop_policy, build_mcp_tools

# 固定候选（模拟 retrieve 召回结果，3 条同品类 SKU）
FIXTURE = [
    {"sku_id": "SKU0001", "title": "简约 925银 项链", "category": "项链", "attributes": {"材质": "925银", "风格": "简约"}},
    {"sku_id": "SKU0006", "title": "浪漫 玫瑰金 项链", "category": "项链", "attributes": {"材质": "玫瑰金", "风格": "浪漫"}},
    {"sku_id": "SKU0008", "title": "简约 18K金 项链", "category": "项链", "attributes": {"材质": "18K金", "风格": "简约"}},
]

# 固定会话集（均为「品类+预算齐备」的成熟句，直接走到推荐）
SESSIONS = [
    "给女朋友买项链，预算2000，喜欢简约",
    "想买条项链送对象，3000以内，要复古风",
    "预算2500，买个浪漫一点的项链",
]

_enc = tiktoken.get_encoding("cl100k_base")
_usage: list[dict] = []  # 每次 LLM 调用的 token 账本


def _msg_text(m) -> str:
    c = getattr(m, "content", None)
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(str(getattr(x, "text", x)) for x in c)
    return str(c)


def _count_messages(messages) -> int:
    return sum(len(_enc.encode(_msg_text(m))) for m in messages)


def _output_text(res) -> str:
    if hasattr(res, "model_dump"):
        return json.dumps(res.model_dump(), ensure_ascii=False)
    return str(res)


class _CountedStructured:
    """包一层 structured runnable，ainvoke 前后记输入/输出 token。"""

    def __init__(self, inner):
        self._inner = inner

    async def ainvoke(self, messages, *a, **k):
        in_tok = _count_messages(messages)
        res = await self._inner.ainvoke(messages, *a, **k)
        _usage.append({"input": in_tok, "output": len(_enc.encode(_output_text(res)))})
        return res


class _CountedClient:
    def __init__(self, inner):
        self._inner = inner

    def with_structured_output(self, model, **kw):
        return _CountedStructured(self._inner.with_structured_output(model, **kw))


def _patch_nodes(real_llm) -> None:
    """把三处节点模块的 get_llm 换成计数包装（节点调用 get_llm() 即被计数）。"""
    clarify_mod.get_llm = lambda: _CountedClient(real_llm)
    route_mod.get_llm = lambda: _CountedClient(real_llm)
    recommend_mod.get_llm = lambda: _CountedClient(real_llm)


def _state(message: str) -> AgentState:
    return {
        "messages": [HumanMessage(message)],
        "preferences": {}, "intent": "", "needs_action": "",
        "candidates": [], "quotes": [], "normalized": [], "recommendation": {},
        "draft_order": None, "order": None,
        "lock_attempts": 0, "price_raised": False, "hop_count": 0,
    }


async def _build_normalized(mcp_tools: dict) -> list[dict]:
    """固定候选 → 真实 MCP 三源报价 → 归一，得到 recommend 用的快照。"""
    cfg = {"configurable": {"mcp_tools": mcp_tools}}
    state = _state("")
    state["candidates"] = FIXTURE
    state["quotes"] = (await compare_node(state, cfg))["quotes"]
    return (await normalize_node(state))["normalized"]


async def profile_full(mcp_tools: dict) -> None:
    from app.agents import llm as llm_mod

    _patch_nodes(llm_mod.get_llm())
    normalized = await _build_normalized(mcp_tools)

    for msg in SESSIONS:
        _usage.clear()
        state = _state(msg)
        # clarify / route 只读最新消息，依次跑并计数
        await clarify_mod.clarify_node(state)
        await route_mod.route_node(state)
        state["normalized"] = normalized
        await recommend_mod.recommend_node(state)
        total_in = sum(u["input"] for u in _usage)
        total_out = sum(u["output"] for u in _usage)
        print(
            f"[{msg}] 调用 {len(_usage)} 次 → 输入 {total_in} tok / 输出 {total_out} tok "
            f"/ 合计 {total_in + total_out}"
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description="token 基线")
    parser.add_argument("--mode", choices=["full", "trimmed"], default="full")
    args = parser.parse_args()
    if args.mode == "trimmed":
        print("trimmed 模式待 M7 落地（结构化裁剪），当前只测 full 基线")
        return 2

    mcp_tools = await build_mcp_tools()
    await profile_full(mcp_tools)
    return 0


if __name__ == "__main__":
    apply_loop_policy()
    raise SystemExit(asyncio.run(main()))