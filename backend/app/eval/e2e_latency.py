"""真端到端延迟评测：跑编译后的完整 StateGraph，量「用户输入 → 推荐返回」的端到端耗时。

与 chaos.py 的差异（重要，简历口径要分清）：
- chaos.py 只跑 compare→normalize 两个非 LLM 节点，产出的是「多源比价+归一环节」延迟（~51ms）；
- 本脚本跑真实图，真实执行
  load_memory → clarify → capture_memory → route → retrieve → compare → normalize → recommend，
  经 graph.astream(stream_mode="updates") 逐节点计时，覆盖真实 LLM 往返、Chroma 检索、
  Postgres 记忆读取、MCP 三源比价，量出「用户输入 → 推荐返回」的端到端延迟。
- confirm 里的 interrupt 由 MemorySaver 内存 checkpoint 承接，计时停在 recommend 节点完成
  那一刻（不测交互式 HITL 下单），即"推荐结果已返回"。

依赖（三者缺一会跑不起来）：
- DASHSCOPE_API_KEY（LLM 与 embedding 共用）；
- PostgreSQL 已 init_db + seed 商品目录（检索走 Chroma+回表，记忆走 profile_memories）；
- MCP 仿真源（价格/券/物流，无外部 key）。
运行：uv run python -m app.eval.e2e_latency
"""
import asyncio
import time
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver

from app.agents.graph import build_graph
from app.agents.state import initial_state
from app.eval._harness import apply_loop_policy, mcp_tools_ctx

# 固定"成熟句"（品类+预算齐备，单轮澄清收敛，走满推荐主链路）
REQUESTS = [
    "想买通勤T恤，预算200，喜欢简约黑色",
    "周末穿的T恤，300以内，要深蓝色纯棉",
    "预算250，买件简约风T恤去旅行",
]
HEALTHY_TRIALS = 3   # 每个请求的试次
FAULT_TRIALS = 3     # 单源 100% 故障的试次（只注入到第一个请求，控制成本）
FAULT_SOURCE = "jd"  # 故障注入的平台


def _override(fail: str | None) -> dict[str, dict] | None:
    """故障注入：把 fail 平台的比价 server 置 100% 失败（与 chaos.py 同款写法）。"""
    if not fail:
        return None
    return {f"price_{fail}": {f"SHOPLENS_FAILRATE_{fail.upper()}": "1"}}


def _p95(samples: list[float]) -> float:
    s = sorted(samples)
    return s[max(0, int(len(s) * 0.95 + 0.5) - 1)] if s else 0.0


def _mean(samples: list[float]) -> float:
    return sum(samples) / len(samples) if samples else 0.0


async def _run_once(graph, tools: dict, request: str, profile_id: str) -> tuple[float, dict, list[str]]:
    """单次真实端到端：返回 (总耗时 ms, {节点: 耗时 ms}, 节点顺序)。

    计时方式：astream 逐节点 yield，用相邻事件时间戳差作该节点耗时；
    总耗时 = 起点到 recommend 完成的时间戳差。
    """
    cfg = {
        "configurable": {
            "thread_id": profile_id,
            "profile_id": profile_id,
            "mcp_tools": tools,
        }
    }
    payload = initial_state(request)

    t0 = time.perf_counter()
    t_prev = t0
    node_ms: dict[str, float] = {}
    order: list[str] = []
    total_ms: float | None = None
    async for chunk in graph.astream(payload, cfg, stream_mode="updates"):
        t = time.perf_counter()
        for node in chunk:
            if node == "__interrupt__":
                continue
            node_ms[node] = (t - t_prev) * 1000
            order.append(node)
            if node == "recommend":
                total_ms = (t - t0) * 1000
        t_prev = t
    if total_ms is None:  # 未走到 recommend（如澄清不收敛提前 END）
        total_ms = (time.perf_counter() - t0) * 1000
    return total_ms, node_ms, order


async def main() -> int:
    graph = build_graph(checkpointer=MemorySaver())

    scenarios: list[tuple[str, list[str], int]] = [
        ("健康", REQUESTS, HEALTHY_TRIALS),
        (f"单源故障({FAULT_SOURCE})", REQUESTS[:1], FAULT_TRIALS),
    ]

    for label, requests, trials in scenarios:
        totals: list[float] = []
        node_aggr: dict[str, list[float]] = {}
        missed = 0
        async with mcp_tools_ctx(_override(None if "健康" in label else FAULT_SOURCE)) as (tools, _registry):
            # 预热一次：吸收 Chroma 集合/embedding 客户端/LLM 客户端首次初始化，不计时
            await _run_once(graph, tools, requests[0], profile_id=f"e2e-warm-{uuid4().hex[:6]}")

            for req in requests:
                for _ in range(trials):
                    total, node_ms, order = await _run_once(
                        graph, tools, req, profile_id=f"e2e-{uuid4().hex[:8]}",
                    )
                    if "recommend" not in order:
                        missed += 1
                        print(f"  [未走满] {req!r} 节点顺序={order}")
                        continue
                    totals.append(total)
                    for node, dur in node_ms.items():
                        node_aggr.setdefault(node, []).append(dur)

        n = len(totals)
        if not n:
            print(f"\n[{label}] 无有效试次（全部未走满 recommend），请检查 clear 收敛/依赖")
            continue
        print(f"\n=== {label} === 有效试次 {n}/{trials * len(requests)}（未走满 {missed}）")
        print(f"端到端（输入→推荐）均值 {_mean(totals):.0f}ms · P95 {_p95(totals):.0f}ms · min {min(totals):.0f}ms · max {max(totals):.0f}ms")
        breakdown = " | ".join(
            f"{node} {_mean(v):.0f}ms" for node, v in node_aggr.items()
        )
        print(f"分节点均值: {breakdown}")
    return 0


if __name__ == "__main__":
    apply_loop_policy()
    raise SystemExit(asyncio.run(main()))