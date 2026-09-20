"""模型工厂：v0 只用 qwen-plus（暂不分层，M7 再拆 qwen-turbo/qwen-plus）。

设计决策：
- DashScope 走 OpenAI 兼容接口，用 langchain-openai 的 ChatOpenAI 接；
- lru_cache 复用单一客户端实例。
"""
from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.core.config import settings


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="qwen-plus",
        api_key=settings.DASHSCOPE_API_KEY,
        base_url=settings.DASHSCOPE_BASE_URL,
        temperature=0.2,
    )