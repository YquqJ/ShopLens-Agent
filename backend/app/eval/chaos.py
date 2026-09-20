"""chaos 基线：单源 100% 失败（故障注入），测源层出结果率 + 端到端完成率 + 延迟。

两层：
1) 源层：逐个 (sku, platform) 直调比价报价，统计「存活源」成功率与单次调用延迟——各源独立子进程，互不影响。
2) 端到端：跑 compare→normalize（v0 无熔断/降级，一源挂则整节点抛错中断），统计完成率与耗时。

v0 预期（问题数字）：存活源源层 100% 出结果，但端到端完成率 0%（无隔离）——此落差即 M6 要修。
    另有：单次 MCP 工具调用 ~530ms（stdio 逐次握手），比价 27 次调用端到端 ≈ 14s，远超 M6「P95<2.5s」目标。

样本量：v0 单次调用就 ~0.5s，故源层每配置采样 2 遍、端到端 2 轮取均值，先立基线；
     M6 优化吞吐后再上调样本量测稳定 P95。
"""
import asyncio
import time

from app.agents.nodes.compare import PRICE_TOOL, compare_node
from app.agents.nodes.normalize import normalize_node
from app.eval._harness import apply_loop_policy, build_mcp_tools

# 固定候选（模拟 retrieve 召回，3 SKU）
CANDIDATES = [
    {"sku_id": "SKU0001", "title": "简约 925银 项链", "category": "项链", "attributes": {"材质": "925银", "风格": "简约"}},
    {"sku_id": "SKU0006", "title": "浪漫 玫瑰金 项链", "category": "项链", "attributes": {"材质": "玫瑰金", "风格": "浪漫"}},
    {"sku_id": "SKU0008", "title": "简约 18K金 项链", "category": "项链", "attributes": {"材质": "18K金", "风格": "简约"}},
]

PLATFORMS = ["jd", "taobao", "pdd"]
_SRC_REPS = 2     # 源层每 (sku,平台) 采样遍数
_E2E_TRIALS = 2   # 端到端每配置轮数（v0 太慢，均值即可立基线）


def _override(fail_source: str | None) -> dict[str, dict]:
    """故障注入：把 fail_source 平台的比价 server 置 100% 失败。"""
    if not fail_source:
        return {}
    return {f"price_{fail_source}": {f"SHOPLENS_FAILRATE_{fail_source.upper()}": "1"}}


def _p95(samples: list[float]) -> float:
    s = sorted(samples)
    return s[max(0, int(len(s) * 0.95 + 0.5) - 1)]


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


async def _run_source_level(tools: dict, fail: str | None) -> tuple[int, int, list[float]]:
    """存活源直调报价：返回 (成功数, 总数, 单次调用延迟 ms 列表)。"""
    sources = [p for p in PLATFORMS if p != fail]
    ok = total = 0
    lat = []
    for _ in range(_SRC_REPS):
        for c in CANDIDATES:
            for p in sources:
                t0 = time.perf_counter()
                try:
                    await tools[PRICE_TOOL[p]].ainvoke({"sku_id": c["sku_id"]})
                    ok += 1
                except Exception:
                    pass
                lat.append((time.perf_counter() - t0) * 1000)
                total += 1
    return ok, total, lat


async def _run_e2e(tools: dict) -> tuple[int, list[float]]:
    """端到端跑 compare→normalize，返回 (完成次数, 各次耗时 ms)。"""
    ok = 0
    lat = []
    cfg = {"configurable": {"mcp_tools": tools}}
    for _ in range(_E2E_TRIALS):
        state = {
            "messages": [], "preferences": {}, "intent": "", "needs_action": "",
            "candidates": CANDIDATES, "quotes": [], "normalized": [],
            "recommendation": {}, "draft_order": None, "order": None,
            "lock_attempts": 0, "price_raised": False, "hop_count": 0,
        }
        t0 = time.perf_counter()
        try:
            quotes = (await compare_node(state, cfg))["quotes"]
            await normalize_node({**state, "quotes": quotes})
            ok += 1
        except Exception:
            pass
        lat.append((time.perf_counter() - t0) * 1000)
    return ok, lat


async def main() -> int:
    for fail in [None, "jd", "taobao", "pdd"]:
        tools = await build_mcp_tools(_override(fail))
        src_ok, src_tot, src_lat = await _run_source_level(tools, fail)
        e2e_ok, e2e_lat = await _run_e2e(tools)
        label = fail or "无故障(基线)"
        src_rate = src_ok / src_tot * 100 if src_tot else 0.0
        print(
            f"[{label}] 存活源出结果率 {src_ok}/{src_tot} ({src_rate:.0f}%)"
            f" 源调用P95 {_p95(src_lat):.0f}ms"
            f" | 端到端完成率 {e2e_ok}/{_E2E_TRIALS} ({e2e_ok / _E2E_TRIALS * 100:.0f}%)"
            f" 端到端均值 {_mean(e2e_lat):.0f}ms"
        )
    return 0


if __name__ == "__main__":
    apply_loop_policy()
    raise SystemExit(asyncio.run(main()))