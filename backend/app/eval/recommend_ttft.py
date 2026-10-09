"""推荐首卡 TTFT 评测。

同一完整 StateGraph 中记录两个时间点：
- recommendation_preview：事实卡片首次可见（当前实现）；
- recommend 更新：LLM 完整理由返回（旧实现中用户首次看到卡片的等价基线）。

运行：uv run python -m app.eval.recommend_ttft
依赖：DASHSCOPE_API_KEY、已初始化的 PostgreSQL/Chroma 和仿真 MCP 源。
"""
import asyncio
import statistics
import time
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver

from app.agents.graph import build_graph
from app.agents.state import initial_state
from app.eval._harness import apply_loop_policy, mcp_tools_ctx

REQUEST = "想买一件男款简约黑色衬衫，预算300元"
WARMUP_RUNS = 1
MEASURED_RUNS = 3
TIMEOUT_SECONDS = 30


async def _measure(graph, tools, label: str) -> tuple[float, float]:
    cfg = {
        "configurable": {
            "thread_id": f"ttft-{label}-{uuid4().hex[:8]}",
            "profile_id": f"ttft-{label}-{uuid4().hex[:8]}",
            "mcp_tools": tools,
        }
    }
    t0 = time.perf_counter()
    preview_ms: float | None = None
    final_ms: float | None = None
    async with asyncio.timeout(TIMEOUT_SECONDS):
        async for mode, event in graph.astream(
            initial_state(REQUEST), cfg, stream_mode=["updates", "custom"],
        ):
            elapsed = (time.perf_counter() - t0) * 1000
            if (
                mode == "custom"
                and isinstance(event, dict)
                and event.get("event") == "recommendation_preview"
                and preview_ms is None
            ):
                preview_ms = elapsed
            elif mode == "updates" and isinstance(event, dict) and "recommend" in event:
                final_ms = elapsed

    if preview_ms is None or final_ms is None:
        raise RuntimeError(f"{label}: recommend path did not produce both preview and final events")
    return preview_ms, final_ms


async def main() -> int:
    graph = build_graph(checkpointer=MemorySaver())
    async with mcp_tools_ctx() as (tools, _registry):
        for index in range(WARMUP_RUNS):
            await _measure(graph, tools, f"warmup-{index + 1}")
        samples = [await _measure(graph, tools, str(index + 1)) for index in range(MEASURED_RUNS)]

    preview = [sample[0] for sample in samples]
    final = [sample[1] for sample in samples]
    p50_preview = statistics.median(preview)
    p50_final = statistics.median(final)
    print(f"有效样本: {len(samples)}；固定请求: {REQUEST}")
    print(f"首卡 TTFT P50: {p50_preview:.0f}ms")
    print(f"完整推荐 P50: {p50_final:.0f}ms")
    print(f"首卡提前: {p50_final - p50_preview:.0f}ms ({(1 - p50_preview / p50_final) * 100:.1f}%)")
    return 0


if __name__ == "__main__":
    apply_loop_policy()
    raise SystemExit(asyncio.run(main()))
