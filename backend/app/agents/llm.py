"""模型工厂：按任务难度分层——route 用 qwen-turbo，clarify/recommend 用 qwen-plus。

设计决策：
- M7 分层实测：route（二分类意图判断）用 qwen-turbo 无碍；但 clarify 若降级到 turbo 会
  「对品类+预算齐备的句子仍误判 needs_clarification=True」，故 clarify 保留 qwen-plus
  （分层按任务难度 + 可靠性实测，不做无脑降级，见 docs/DESIGN_NOTES）；
- lru_cache 按 model 分键，各自复用单一客户端实例。
"""
from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.core.config import settings

# 分层模型（SPEC 锁定）：轻量 = 澄清/路由，主 = 推荐
LIGHT_MODEL = "qwen-turbo"
MAIN_MODEL = "qwen-plus"


@lru_cache(maxsize=8)
def get_llm(model: str = MAIN_MODEL) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        api_key=settings.DASHSCOPE_API_KEY,
        base_url=settings.DASHSCOPE_BASE_URL,
        temperature=0.2,
    )