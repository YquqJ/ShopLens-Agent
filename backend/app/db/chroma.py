"""Chroma 持久化客户端（只做向量检索）。

设计决策：
- embedded 模式：持久化到本地目录，无独立容器（见 SPEC 部署约定）；
- 全局单例 client，惰性初始化（import 时不在磁盘建目录，首次 get_client 才落盘）；
- Chroma 只存「商品向量 + 元数据」，订单等关系数据走 Postgres，职责不重叠。
"""
import chromadb

from app.core.config import settings

_client: chromadb.ClientAPI | None = None


def get_client() -> chromadb.ClientAPI:
    """返回全局 Chroma 持久化客户端；首次调用时初始化并落盘建目录。"""
    global _client
    if _client is None:
        settings.chroma_path.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(settings.chroma_path))
    return _client


def get_collection(name: str, embedding_function=None) -> chromadb.Collection:
    """按名取/建 collection；embedding_function 用于向量化（M1 起传入 text-embedding-v3）。"""
    return get_client().get_or_create_collection(
        name=name, embedding_function=embedding_function
    )