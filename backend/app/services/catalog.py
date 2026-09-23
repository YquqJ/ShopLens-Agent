"""商品目录服务：seed 生成、向量化、语义检索、存量校验。

职责划分（配合 tools/catalog.py 的 @tool 薄封装）：
- build_seed_products()：确定性生成服饰 SKU（属性/价格/库存），
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
from sqlalchemy import select, text

from app.core.config import settings
from app.core.loop import install_selector_event_loop_policy
from app.db import chroma as chroma_db
from app.db.models import Product
from app.db.session import async_session_factory, init_db

# Chroma 商品集合名
COLLECTION_NAME = "products"

# DashScope text-embedding-v3 单次请求 input 上限（保守取 10），seed 分批灌入
_EMBED_BATCH = 10

# ---- seed 配置：服饰类目 → (可复用属性维度, 基准价) ----
# 四个属性键在类目间保持一致，取值按类目区分，避免不合理的材质组合。
COLORS = ["黑色", "白色", "深蓝", "米白", "卡其"]
T_SHIRT_ATTRIBUTES = {
    "颜色": COLORS,
    "风格": ["简约", "街头", "运动"],
    "材质": ["纯棉", "亚麻"],
    "场景": ["周末", "旅行", "运动"],
}
SHIRT_ATTRIBUTES = {
    "颜色": COLORS,
    "风格": ["简约", "通勤", "复古"],
    "材质": ["纯棉", "亚麻", "牛仔"],
    "场景": ["通勤", "约会", "旅行"],
}
SWEATSHIRT_ATTRIBUTES = {
    "颜色": COLORS,
    "风格": ["简约", "街头", "运动"],
    "材质": ["纯棉", "抓绒"],
    "场景": ["周末", "旅行", "运动"],
}
OUTERWEAR_ATTRIBUTES = {
    "颜色": COLORS,
    "风格": ["简约", "通勤", "复古"],
    "材质": ["羊毛", "牛仔", "棉服"],
    "场景": ["通勤", "旅行", "约会"],
}
TROUSER_ATTRIBUTES = {
    "颜色": COLORS,
    "风格": ["简约", "通勤", "街头"],
    "材质": ["纯棉", "亚麻", "牛仔"],
    "场景": ["通勤", "周末", "旅行"],
}
SHOE_ATTRIBUTES = {
    "颜色": COLORS,
    "风格": ["简约", "复古", "运动"],
    "材质": ["真皮", "帆布", "网面"],
    "场景": ["通勤", "周末", "运动"],
}

# 每个类目：统一属性维度 + 基准价（元）
CATALOG_SPECS: list[tuple[str, dict[str, list[str]], float]] = [
    ("T恤", T_SHIRT_ATTRIBUTES, 180),
    ("衬衫", SHIRT_ATTRIBUTES, 260),
    ("卫衣", SWEATSHIRT_ATTRIBUTES, 320),
    ("外套", OUTERWEAR_ATTRIBUTES, 680),
    ("裤装", TROUSER_ATTRIBUTES, 360),
    ("鞋", SHOE_ATTRIBUTES, 480),
]


def build_seed_products(count: int = 200) -> list[dict]:
    """确定性生成 count 条 SKU（固定随机种子，价格/库存可复现）。"""
    rng = random.Random(42)
    products: list[dict] = []
    category_counts: dict[str, int] = {}
    for i in range(1, count + 1):
        category, dims, base_price = CATALOG_SPECS[(i - 1) % len(CATALOG_SPECS)]
        category_index = category_counts.get(category, 0)
        category_counts[category] = category_index + 1
        # 按类目内序号轮转属性，让每个服饰类目都有可测试的颜色/风格/材质/场景覆盖。
        attributes = {
            key: values[(category_index + offset) % len(values)]
            for offset, (key, values) in enumerate(dims.items())
        }
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


async def seed(count: int = 200) -> int:
    """SKU 全量写入 Postgres + 向量化进 Chroma（幂等重灌），返回 SKU 数。"""
    products = build_seed_products(count)

    # 1) Postgres：清空重灌商品主数据（价格/库存唯一事实来源）
    #    TRUNCATE RESTART IDENTITY 重置自增序列，让 id 每次从 1 重新编号（DELETE 不会重置）
    async with async_session_factory() as session:
        await session.execute(text("TRUNCATE TABLE products RESTART IDENTITY"))
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
