"""chaos 基线/改善对照：单源 100% 失败（故障注入），测源层出结果率 + 端到端完成率 + 延迟 + 错误预算。

两层：
1) 源层：逐个 (sku, platform) 直调比价报价，统计「存活源」成功率与单次调用延迟——各源独立子进程，互不影响。
2) 端到端：跑 compare→normalize（M6 起有熔断/降级），统计完成率与耗时。

M6 改善对照（对比 v0 基线见 docs/RESUME.md）：
- v0：单源失败 → 端到端完成率 0%（无隔离）；M6：单源失败 → 其余源端到端 100% 出结果。
- 错误预算：存活源错误率零新增（失败源想隔离，不向其余源扩散）。
"""
import asyncio
import time

from app.agents.nodes.compare import PRICE_TOOL, compare_node
from app.agents.nodes.normalize import normalize_node
from app.eval._harness import apply_loop_policy, mcp_tools_ctx

# 固定候选（模拟 retrieve 召回，3 SKU）
CANDIDATES = [
    {"sku_id": "SKU0001", "title": "黑色 简约 纯棉 周末 T恤", "category": "T恤", "attributes": {"颜色": "黑色", "风格": "简约", "材质": "纯棉", "场景": "周末"}},
    {"sku_id": "SKU0007", "title": "白色 简约 纯棉 周末 T恤", "category": "T恤", "attributes": {"颜色": "白色", "风格": "简约", "材质": "纯棉", "场景": "周末"}},
    {"sku_id": "SKU0013", "title": "深蓝 简约 纯棉 周末 T恤", "category": "T恤", "attributes": {"颜色": "深蓝", "风格": "简约", "材质": "纯棉", "场景": "周末"}},
]

PLATFORMS = ["jd", "taobao", "pdd"]
HEALTHY_TRIALS = 20
FAIL_TRIALS = 5
_SRC_REPS = 2


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


async def _run_e2e(tools: dict, trials: int) -> tuple[int, list[float]]:
    """端到端跑 compare→normalize，返回 (完成次数, 各次耗时 ms)。"""
    ok = 0
    lat = []
    cfg = {"configurable": {"mcp_tools": tools}}
    for _ in range(trials):
        state = {
            "messages": [], "preferences": {}, "profile_preferences": {}, "memory_events": [],
            "intent": "", "needs_action": "", "clarify_question": "", "reply": "",
            "zdm_mode": False,
            "candidates": CANDIDATES, "quotes": [], "normalized": [],
            "degraded_sources": [], "recommendation": {},
            "draft_order": None, "order": None,
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
        async with mcp_tools_ctx(_override(fail)) as (tools, registry):
            trials = HEALTHY_TRIALS if fail is None else FAIL_TRIALS
            src_ok, src_tot, src_lat = await _run_source_level(tools, fail)
            e2e_ok, e2e_lat = await _run_e2e(tools, trials)
            label = fail or "无故障(基线)"
            src_rate = src_ok / src_tot * 100 if src_tot else 0.0
            print(
                f"[{label}] 存活源出结果率 {src_ok}/{src_tot} ({src_rate:.0f}%)"
                f" 源调用P95 {_p95(src_lat):.0f}ms"
                f" | 端到端完成率 {e2e_ok}/{trials} ({e2e_ok / trials * 100:.0f}%)"
                f" 端到端均值 {_mean(e2e_lat):.0f}ms/P95 {_p95(e2e_lat):.0f}ms"
            )
            # 错误预算（存活源错误率应零新增）
            for src in PLATFORMS:
                b = registry.report().get(src)
                if b:
                    print(
                        f"     {src}: 成功{b['success']} 失败{b['failure']} 熔断跳过{b['skipped']}"
                        f" 错误率{b['error_rate']} 预算剩余{b['error_budget_remaining']}"
                    )
    return 0


if __name__ == "__main__":
    apply_loop_policy()
    raise SystemExit(asyncio.run(main()))
