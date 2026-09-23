"""比价黄金集判定：用 normalize() 跑 golden_set.json，规则判定最优解命中率。

设计决策：
- 纯离线，不碰 LLM / MCP / Postgres / Chroma，只测 services/normalize.py 的价比规则；
- golden 值为人工手算，judge 另用独立参考实现（reference）复核 golden 标注，
  标注与参考不一致时视为「黄金集自身可疑」而非代码 bug（单独告警，不计入命中率）；
- 命中率 = 判定一致条目 / 总条目，作为 M5 基线，也是 M6/M7 改 normalize 后的回归护栏。
"""
import json
from pathlib import Path

from app.services.normalize import normalize, TIME_VALUE_PER_DAY

GOLDEN = Path(__file__).with_name("golden_set.json")
_EPS = 0.01


def _discount_price(price: float, coupon: dict | None) -> float:
    """独立参考实现：券满 threshold 才减。"""
    if not coupon:
        return 0.0
    return coupon["amount"] if price >= coupon["threshold"] else 0.0


def reference_best(quotes: list[dict]) -> dict:
    """独立重算某 SKU 的最优平台（与 normalize 同规则，故意不复用其代码）。"""
    best = None
    for q in quotes:
        d = _discount_price(q["price"], q["coupon"])
        final = round(q["price"] - d + q["shipping_fee"], 2)
        cost = round(final + q["eta_days"] * TIME_VALUE_PER_DAY, 2)
        if best is None or cost < best["cost"]:
            best = {"platform": q["platform"], "final": final, "cost": cost}
    return best


def _warn_selfcheck(case: dict) -> None:
    """复核 golden 标注是否与独立参考一致（仅告警，不阻断）。"""
    by_sku: dict[str, list] = {}
    for q in case["quotes"]:
        by_sku.setdefault(q["sku_id"], []).append(q)
    for g in case["golden"]:
        ref = reference_best(by_sku[g["sku_id"]])
        if (
            abs(ref["final"] - g["final_price"]) > _EPS
            or abs(ref["cost"] - g["total_cost"]) > _EPS
            or ref["platform"] != g["best_platform"]
        ):
            print(
                f"[selfcheck] {case['id']} {g['sku_id']} 黄金标注与参考不符："
                f"参考={ref} 标注=平台{g['best_platform']}/价{g['final_price']}/成本{g['total_cost']}"
            )


def main() -> int:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    total = passed = 0
    failures: list[str] = []

    for case in data:
        _warn_selfcheck(case)
        items = {i["sku_id"]: i for i in normalize(case["quotes"], case["candidates"])}
        for g in case["golden"]:
            total += 1
            item = items.get(g["sku_id"])
            if item is None:
                failures.append(f"{case['id']} {g['sku_id']} 未在归一结果中出现")
                print(f"[FAIL] {case['id']} {g['sku_id']}: normalize 结果缺失")
                continue
            b = item["best"]
            ok_platform = b["platform"] == g["best_platform"]
            ok_final = abs(b["final_price"] - g["final_price"]) <= _EPS
            ok_cost = abs(b["total_cost"] - g["total_cost"]) <= _EPS
            marks = "".join("." if ok else "X" for ok in (ok_platform, ok_final, ok_cost))
            platform = b["platform"] if ok_platform else f"{b['platform']}!={g['best_platform']}"
            print(
                f"[{case['id']}] {g['sku_id']}: "
                f"平台 {platform} 到手价 {b['final_price']} 成本 {b['total_cost']}  {marks}"
            )
            if not (ok_platform and ok_final and ok_cost):
                failures.append(f"{case['id']} {g['sku_id']}")

        # 多 SKU 案例校验排序：normalize 应按综合成本升序返回
        if len(case["golden"]) > 1:
            ordered = normalize(case["quotes"], case["candidates"])
            costs = [i["best"]["total_cost"] for i in ordered]
            if costs != sorted(costs):
                failures.append(f"{case['id']} 未按综合成本升序")
                print(f"[FAIL] {case['id']} 排序异常: {costs}")

    passed = total - len(failures)
    rate = passed / total * 100 if total else 0.0
    print(f"\n比价黄金集命中率: {passed}/{total} ({rate:.1f}%)")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
