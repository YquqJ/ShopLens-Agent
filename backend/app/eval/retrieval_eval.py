"""检索尺子：硬约束命中率 + Recall@k（before 纯语义 vs after 语义+硬过滤）。

设计决策：
- 相关性用规则判定：满足 retrieval_set.json 手写闭集硬约束的目录项 = 相关，
  非 LLM、非人工标注，延续「黄金集 + 规则客观判定」基因；
- 四把尺子里唯一真正跑检索的集成评测：需 Postgres + Chroma + text-embedding-v3
  （DASHSCOPE_API_KEY），seed 后离线跑；
- v1 只量化「结构硬约束是否被遵守」（纯语义会召回材质/颜色/品类错的商品）；
  语义排序质量（"显瘦→修身更靠前"）需 rerank，留后续评测再量，不在此虚报。
"""
import asyncio
import json
import sys
from pathlib import Path

from app.core.loop import install_selector_event_loop_policy
from app.db.session import init_db
from app.services import catalog

SET = Path(__file__).with_name("retrieval_set.json")
_SEED_COUNT = 200

# 约束 ASCII key → 商品属性中文 key（build_seed_products 的 attributes 用中文键）
_CN_KEYS = {
    "color": "颜色", "style": "风格", "material": "材质", "scene": "场景",
    "fit": "版型", "gender": "性别", "size": "尺码",
}


def _matches(item: dict, constraints: dict) -> bool:
    """规则判定：item 是否满足全部闭集硬约束（category 顶层 + 其余中文属性键）。"""
    for ascii_key, value in (constraints or {}).items():
        if value in (None, "", []):
            continue
        if ascii_key == "category":
            if item.get("category") != value:
                return False
            continue
        cn_key = _CN_KEYS.get(ascii_key)
        if cn_key is None:
            continue
        actual = (item.get("attributes") or {}).get(cn_key)
        if isinstance(value, list):
            if actual not in value:
                return False
        elif actual != value:
            return False
    return True


def _metrics(top_items: list[dict], products: list[dict], constraints: dict) -> tuple[float, float]:
    """返回 (Recall@k, 硬约束命中率)；top_items 为空时命中率记 0。"""
    relevant = {p["sku_id"] for p in products if _matches(p, constraints)}
    top = top_items or []
    top_skus = [c["sku_id"] for c in top]
    recall = (len(set(top_skus) & relevant) / len(relevant)) if relevant else 0.0
    hits = sum(1 for c in top if _matches(c, constraints))
    hit_rate = hits / len(top) if top else 0.0
    return recall, hit_rate


async def main() -> int:
    await init_db()
    await catalog.seed(_SEED_COUNT)
    products = catalog.build_seed_products(_SEED_COUNT)
    cases = json.loads(SET.read_text(encoding="utf-8"))["cases"]

    base_recalls, filt_recalls = [], []
    base_hits, filt_hits = [], []
    print("检索尺子（before=纯语义 / after=语义+硬过滤）：\n")
    for case in cases:
        constraints = case.get("constraints", {})
        top_k = case.get("top_k", 5)
        query = case["query"]

        base = await catalog.search(query, top_k=top_k)
        filt = await catalog.search(query, top_k=top_k, filters=constraints)

        b_recall, b_hit = _metrics(base, products, constraints)
        f_recall, f_hit = _metrics(filt, products, constraints)
        base_recalls.append(b_recall)
        filt_recalls.append(f_recall)
        base_hits.append(b_hit)
        filt_hits.append(f_hit)

        print(
            f"[{case['id']}] {query!r}\n"
            f"    约束 {constraints}\n"
            f"    before 纯语义: Recall@{top_k}={b_recall:.0%} 硬约束命中率={b_hit:.0%}\n"
            f"    after  硬过滤: Recall@{top_k}={f_recall:.0%} 硬约束命中率={f_hit:.0%}"
        )

    print("\n汇总均值：")
    print(f"  Recall@{5}:  {sum(base_recalls)/len(base_recalls):.0%} -> {sum(filt_recalls)/len(filt_recalls):.0%}")
    print(f"  硬约束命中率: {sum(base_hits)/len(base_hits):.0%} -> {sum(filt_hits)/len(filt_hits):.0%}")
    return 0


if __name__ == "__main__":
    install_selector_event_loop_policy()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台 GBK 打花中文，这里固定 UTF-8 输出
    raise SystemExit(asyncio.run(main()))
