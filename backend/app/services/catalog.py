"""商品目录服务：seed 生成、向量化、语义检索、存量校验。

职责划分（配合 tools/catalog.py 的 @tool 薄封装）：
- build_seed_products()：确定性生成 ~100 SKU（首饰 + 3C 多类目，属性/价格/库存），
  价格/库存在此固化，随后写入 Postgres；
- seed()：SKU 写入 Postgres(products 表) + 向量化进 Chroma(products 集合)；
- search()：query 先 Chroma 语义检索召回候选，再用 sku_id 回 Postgres 取权威价格/库存；
- check_stock_price()：按 sku_id 读 Postgres 实时价格/库存。

设计决策：
- Chroma 只存「向量 + 最小元数据」，价格/库存唯一事实来源是 Postgres——
  保证「价格/库存来自落库，非 LLM 现编」；
- 向量检索 v0 只做 Chroma 语义检索（无 rerank）；gte-rerank 重排留到 M7 优化，
  先用四把尺子量出 v0 基线再对症加。
"""
import asyncio
import json
import random

from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.loop import install_selector_event_loop_policy
from app.db import chroma as chroma_db
from app.db.models import Product
from app.db.session import async_session_factory, init_db

# Chroma 商品集合名
COLLECTION_NAME = "products"

# DashScope text-embedding-v3 单次请求 input 上限（保守取 10），seed 分批灌入
_EMBED_BATCH = 10

# ---- seed 配置：类目 → (属性维度{键: 候选值}, 基准价) ----
# 首饰沿用 材质/风格；3C 用品 牌/存储 等。价格 = 基准价 × 0.85~1.15 波动，固定种子保证可复现。
MATERIALS = ["925银", "18K金", "玫瑰金", "黄金", "铂金"]
STYLES = ["简约", "复古", "浪漫", "时尚"]

# 每个类目：属性维度（键 → 候选值）+ 基准价（元）
CATALOG_SPECS: list[tuple[str, dict[str, list[str]], float]] = [
    # ---- 首饰 ----
    ("项链", {"材质": MATERIALS, "风格": STYLES}, 1800),
    ("手链", {"材质": MATERIALS, "风格": STYLES}, 1200),
    ("戒指", {"材质": MATERIALS, "风格": STYLES}, 1500),
    ("耳环", {"材质": MATERIALS, "风格": STYLES}, 800),
    ("手镯", {"材质": MATERIALS, "风格": STYLES}, 1400),
    ("胸针", {"材质": MATERIALS, "风格": STYLES}, 600),
    # ---- 3C ----
    ("手机", {"品牌": ["Apple", "华为", "小米", "OPPO", "vivo", "荣耀"], "存储": ["128G", "256G", "512G", "1T"]}, 4500),
    ("耳机", {"品牌": ["Apple", "索尼", "Bose", "华为", "小米"], "类型": ["入耳式", "头戴式", "真无线"]}, 900),
    ("平板", {"品牌": ["Apple", "华为", "小米", "联想"], "存储": ["64G", "128G", "256G"]}, 3200),
    ("智能手表", {"品牌": ["Apple", "华为", "小米", "Amazfit"], "版本": ["标准版", "运动版"]}, 1600),
    ("蓝牙音箱", {"品牌": ["索尼", "Bose", "JBL", "小米"], "类型": ["便携", "桌面", "户外"]}, 500),
]


def build_seed_products(count: int = 100) -> list[dict]:
    """确定性生成 count 条 SKU（固定随机种子，价格/库存可复现）。"""
    rng = random.Random(42)
    products: list[dict] = []
    for i in range(1, count + 1):
        category, dims, base_price = CATALOG_SPECS[(i - 1) % len(CATALOG_SPECS)]
        # 每属性维度按同一偏移挑候选值，保证同 SKU 属性组合确定
        attributes = {key: values[(i - 1) % len(values)] for key, values in dims.items()}
        price = round(base_price * rng.uniform(0.85, 1.15), 2)
        stock = rng.randint(0, 120)  # 含缺货样本，供 check_stock_price 校验
        sku_id = f"SKU{i:04d}"
        title = " ".join(str(v) for v in attributes.values()) + f" {category}"
        products.append({
            "sku_id": sku_id,
            "title": title,
            "category": category,
            "attributes": attributes,
            "price": price,
            "stock": stock,
            "image_url": f"https://picsum.photos/seed/{sku_id.lower()}/400/400",
        })
    return products


def build_embedding_fn() -> OpenAIEmbeddingFunction:
    """text-embedding-v3（DashScope OpenAI 兼容）embedding 函数。"""
    if not settings.DASHSCOPE_API_KEY:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置，无法调用 text-embedding-v3（请在项目根 .env 填写）")
    return OpenAIEmbeddingFunction(
        api_key=settings.DASHSCOPE_API_KEY,
        model_name="text-embedding-v3",
        api_base=settings.DASHSCOPE_BASE_URL,
    )


async def seed(count: int = 100) -> int:
    """SKU 全量写入 Postgres + 向量化进 Chroma（幂等重灌），返回 SKU 数。"""
    products = build_seed_products(count)

    # 1) Postgres：清空重灌商品主数据（价格/库存唯一事实来源）
    async with async_session_factory() as session:
        await session.execute(delete(Product))
        session.add_all(Product(**p) for p in products)
        await session.commit()

    # 2) Chroma：幂等清空后重灌「向量 + 最小元数据」（召回用，不含价格/库存）
    col = chroma_db.get_collection(COLLECTION_NAME, embedding_function=build_embedding_fn())
    old = col.get(include=[])
    if old["ids"]:
        col.delete(ids=old["ids"])
    # 分批灌入：text-embedding-v3 单次 input 有上限，openai 兼容客户端不做自动分批
    for i in range(0, len(products), _EMBED_BATCH):
        chunk = products[i:i + _EMBED_BATCH]
        col.add(
            ids=[p["sku_id"] for p in chunk],
            documents=[p["title"] for p in chunk],
            metadatas=[
                {
                    "sku_id": p["sku_id"],
                    "title": p["title"],
                    "category": p["category"],
                    "attributes": json.dumps(p["attributes"], ensure_ascii=False),
                }
                for p in chunk
            ],
        )
    return len(products)


async def _fetch_by_skus(sku_ids: list[str]) -> list[Product]:
    """按 sku_id 批量读 Postgres 商品主数据。"""
    async with async_session_factory() as session:
        result = await session.execute(select(Product).where(Product.sku_id.in_(sku_ids)))
        return list(result.scalars())


async def search(query: str, top_k: int = 5) -> list[dict]:
    """语义检索：Chroma 召回 -> 回 Postgres 取权威价格/库存 -> 组装结果。"""
    col = chroma_db.get_collection(COLLECTION_NAME, embedding_function=build_embedding_fn())
    res = col.query(query_texts=[query], n_results=top_k, include=["metadatas", "distances"])
    metadatas = res["metadatas"][0]
    distances = res["distances"][0]
    sku_ids = [md["sku_id"] for md in metadatas]

    by_sku = {p.sku_id: p for p in await _fetch_by_skus(sku_ids)}

    results: list[dict] = []
    for sku_id, dist in zip(sku_ids, distances):
        p = by_sku.get(sku_id)
        if p is None:
            continue
        results.append({
            "sku_id": p.sku_id,
            "title": p.title,
            "category": p.category,
            "attributes": p.attributes,
            "price": float(p.price),
            "stock": p.stock,
            "image_url": p.image_url,
            "score": round(1 - dist, 4),  # cosine distance -> 相似度
        })
    return results


async def check_stock_price(sku_id: str) -> dict | None:
    """按 sku_id 读 Postgres 实时价格/库存；不存在返回 None。"""
    async with async_session_factory() as session:
        result = await session.execute(select(Product).where(Product.sku_id == sku_id))
        p = result.scalar_one_or_none()
    if p is None:
        return None
    return {
        "sku_id": p.sku_id,
        "title": p.title,
        "price": float(p.price),
        "stock": p.stock,
        "in_stock": p.stock > 0,
    }


async def _main() -> int:
    """seed 入口：先建表，再灌数据。"""
    await init_db()
    return await seed()


if __name__ == "__main__":
    install_selector_event_loop_policy()
    n = asyncio.run(_main())
    print(f"seed 完成：{n} 条 SKU 已写入 Postgres + Chroma")