"""多源比价串行/并发 A/B 基准。

固定相同候选、复用同一组持久 MCP Session，并在每轮交替执行串行和并发，
测量 compare 节点本身的耗时；不包含 LLM、检索、建连或首次预热。

运行：uv run python -m app.eval.compare_concurrency
"""
import asyncio
import time

from app.agents.nodes.compare import compare_node
from app.eval._harness import apply_loop_policy, mcp_tools_ctx

CANDIDATES = [
    {"sku_id": "SKU0001", "price": 129},
    {"sku_id": "SKU0007", "price": 159},
    {"sku_id": "SKU0013", "price": 189},
    {"sku_id": "SKU0019", "price": 219},
    {"sku_id": "SKU0025", "price": 249},
]
WARMUP_RUNS = 2
MEASURED_RUNS = 20
SERIAL_CONCURRENCY = 1
PARALLEL_CONCURRENCY = 6


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, int(len(ordered) * 0.95 + 0.5) - 1)]


def _median(samples: list[float]) -> float:
    ordered = sorted(samples)
    middle = len(ordered) // 2
    return (ordered[middle - 1] + ordered[middle]) / 2 if len(ordered) % 2 == 0 else ordered[middle]


async def _measure(tools: dict, concurrency: int) -> float:
    t0 = time.perf_counter()
    result = await compare_node(
        {"candidates": CANDIDATES},
        {"configurable": {"mcp_tools": tools, "compare_concurrency": concurrency}},
    )
    if len(result["quotes"]) != len(CANDIDATES) * 3:
        raise RuntimeError(f"expected {len(CANDIDATES) * 3} quotes, got {len(result['quotes'])}")
    return (time.perf_counter() - t0) * 1000


async def main() -> int:
    samples = {SERIAL_CONCURRENCY: [], PARALLEL_CONCURRENCY: []}
    async with mcp_tools_ctx() as (tools, _registry):
        for concurrency in (SERIAL_CONCURRENCY, PARALLEL_CONCURRENCY):
            for _ in range(WARMUP_RUNS):
                await _measure(tools, concurrency)

        for run in range(MEASURED_RUNS):
            order = (SERIAL_CONCURRENCY, PARALLEL_CONCURRENCY)
            if run % 2:
                order = tuple(reversed(order))
            for concurrency in order:
                samples[concurrency].append(await _measure(tools, concurrency))

    serial = samples[SERIAL_CONCURRENCY]
    parallel = samples[PARALLEL_CONCURRENCY]
    median_reduction = (1 - _median(parallel) / _median(serial)) * 100
    p95_reduction = (1 - _p95(parallel) / _p95(serial)) * 100
    print(f"候选 SKU: {len(CANDIDATES)}，每次 {len(CANDIDATES) * 3 * 3} 次 MCP 调用，已排除建连预热")
    print(f"串行(并发={SERIAL_CONCURRENCY}): Median {_median(serial):.1f}ms | P95 {_p95(serial):.1f}ms")
    print(f"并发(并发={PARALLEL_CONCURRENCY}): Median {_median(parallel):.1f}ms | P95 {_p95(parallel):.1f}ms")
    print(f"延迟降幅: Median {median_reduction:.1f}% | P95 {p95_reduction:.1f}%")
    return 0


if __name__ == "__main__":
    apply_loop_policy()
    raise SystemExit(asyncio.run(main()))
