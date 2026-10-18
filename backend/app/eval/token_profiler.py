"""token 基线/改善对照：固定会话集跑 clarify/route/recommend 三处 LLM 调用，统计 token。

设计决策：
- M5 基线 ~755 tok/会话（全量：qwen-plus 三节点 + 字典 repr 全文上下文）；
  M7 起实测「分层（route→qwen-flash，clarify/recommend→qwen-plus）+ 结构化裁剪（推荐上下文紧凑化）」后的数字，
  与 M5 基线对比产出降幅；
- 计数用 tiktoken cl100k_base 近似（Qwen 官方分词器略有出入，仅作相对对比）：
  输入侧由节点消息精确重建，输出侧用结构化结果的 model_dump json 近似；
- recommend 上下文来自固定候选 + 真实 MCP 报价归一的快照，不依赖 Chroma/Postgres。
"""
import asyncio
import json

import tiktoken
from langchain_core.messages import HumanMessage

from app.agents.nodes import clarify as clarify_mod
from app.agents.nodes import recommend as recommend_mod
from app.agents.nodes import route as route_mod
from app.agents.nodes.compare import compare_node
from app.agents.nodes.normalize import normalize_node
from app.agents.state import AgentState
from app.eval._harness import apply_loop_policy, mcp_tools_ctx

# 固定候选（模拟 retrieve 召回结果，3 条同品类 SKU）
FIXTURE = [
    {"sku_id": "SKU0001", "title": "黑色 简约 纯棉 周末 T恤", "category": "T恤", "attributes": {"颜色": "黑色", "风格": "简约", "材质": "纯棉", "场景": "周末"}},
    {"sku_id": "SKU0007", "title": "白色 简约 纯棉 周末 T恤", "category": "T恤", "attributes": {"颜色": "白色", "风格": "简约", "材质": "纯棉", "场景": "周末"}},
    {"sku_id": "SKU0013", "title": "深蓝 简约 纯棉 周末 T恤", "category": "T恤", "attributes": {"颜色": "深蓝", "风格": "简约", "材质": "纯棉", "场景": "周末"}},
]

# 固定会话集（均为「品类+预算齐备」的成熟句，直接走到推荐）
SESSIONS = [
    "想买通勤T恤，预算200，喜欢简约黑色",
    "周末穿的T恤，300以内，要深蓝色纯棉",
    "预算250，买件简约风T恤去旅行",
]

# 代表性偏好（供「裁剪前/后」上下文 token 对照）
SAMPLE_PREFS = {"category": "T恤", "budget": 200.0, "style": "简约", "color": "黑色", "material": "纯棉"}

# M5 全量基线（实测，用于对照）
BASELINE_TOTAL = 755

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


def _make_counted_structured(factory):
    """把节点模块的 get_structured_llm 替换为计数包装（透传 model/schema 参数）。"""

    def counted(model, schema):
        return _CountedStructured(factory(model, schema))

    return counted


def _patch_nodes() -> None:
    from app.agents import llm as llm_mod

    clarify_mod.get_structured_llm = _make_counted_structured(llm_mod.get_structured_llm)
    route_mod.get_structured_llm = _make_counted_structured(llm_mod.get_structured_llm)
    recommend_mod.get_structured_llm = _make_counted_structured(llm_mod.get_structured_llm)


def _state(message: str) -> AgentState:
    return {
        "messages": [HumanMessage(message)],
        "preferences": {}, "profile_preferences": {}, "memory_events": [],
        "intent": "", "needs_action": "", "clarify_question": "", "reply": "",
        "zdm_mode": False,
        "candidates": [], "quotes": [], "normalized": [], "degraded_sources": [],
        "recommendation": {}, "draft_order": None, "order": None,
        "lock_attempts": 0, "price_raised": False, "hop_count": 0,
    }


async def _build_normalized(mcp_tools: dict) -> list[dict]:
    """固定候选 → 真实 MCP 三源报价 → 归一，得到 recommend 用的快照。"""
    cfg = {"configurable": {"mcp_tools": mcp_tools}}
    state = _state("")
    state["candidates"] = FIXTURE
    state["quotes"] = (await compare_node(state, cfg))["quotes"]
    return (await normalize_node(state))["normalized"]


async def profile_current(mcp_tools: dict) -> list[int]:
    """跑当前（M7 分层+裁剪）管线，返回每会话合计 token。"""
    _patch_nodes()
    normalized = await _build_normalized(mcp_tools)
    totals = []
    for msg in SESSIONS:
        _usage.clear()
        state = _state(msg)
        await clarify_mod.clarify_node(state)
        await route_mod.route_node(state)
        state["normalized"] = normalized
        await recommend_mod.recommend_node(state)
        total_in = sum(u["input"] for u in _usage)
        total_out = sum(u["output"] for u in _usage)
        totals.append(total_in + total_out)
        print(
            f"[{msg}] 调用 {len(_usage)} 次 → 输入 {total_in} tok / 输出 {total_out} tok "
            f"/ 合计 {total_in + total_out}"
        )
    return totals


def show_ctx_delta(normalized: list[dict]) -> None:
    """结构化裁剪：同一快照下，推荐上下文「v0 全文 vs M7 紧凑」的输入 token 对照。"""
    top = recommend_mod.select_top(normalized, SAMPLE_PREFS.get("budget"))
    verbose = recommend_mod.build_user(SAMPLE_PREFS, top, [], compact=False)
    compact = recommend_mod.build_user(SAMPLE_PREFS, top, [], compact=True)
    v_tok = len(_enc.encode(verbose))
    c_tok = len(_enc.encode(compact))
    print(f"[裁剪对照] 推荐上下文输入 token：v0 全文 {v_tok} → M7 紧凑 {c_tok}（降 {(1 - c_tok / v_tok) * 100:.0f}%）")


async def main() -> int:
    async with mcp_tools_ctx() as (mcp_tools, _registry):
        normalized = await _build_normalized(mcp_tools)
        show_ctx_delta(normalized)
        totals = await profile_current(mcp_tools)
        avg = sum(totals) / len(totals)
        drop = (1 - avg / BASELINE_TOTAL) * 100
        print(f"\n当前单次会话平均合计 {avg:.0f} tok（M5 全量基线 {BASELINE_TOTAL}）→ 降 {drop:.0f}%")
    return 0


if __name__ == "__main__":
    apply_loop_policy()
    raise SystemExit(asyncio.run(main()))
