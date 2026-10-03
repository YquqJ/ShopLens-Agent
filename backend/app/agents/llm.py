"""模型工厂：按任务难度分层——route 用 qwen-turbo，clarify/recommend 用 qwen-plus。

设计决策：
- M7 分层实测：route（二分类意图判断）用 qwen-turbo 无碍；但 clarify 若降级到 turbo 会
  「对品类+预算齐备的句子仍误判 needs_clarification=True」，故 clarify 保留 qwen-plus
  （分层按任务难度 + 可靠性实测，不做无脑降级，见 docs/DESIGN_NOTES）；
- lru_cache 按 model 分键，各自复用单一客户端实例。
"""
import json
from functools import lru_cache

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

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


def get_structured_llm(model: str, schema: type[BaseModel]):
    """以 json_object 做结构化输出（替代默认 function calling），返回 .ainvoke(messages) -> schema 实例。

    设计决策：DashScope 的 qwen 走 function calling 会异常慢甚至挂起（实测 >60s 超时），
    而 response_format=json_object 的纯文本生成与裸聊同速（~1.8s）且解析正确。
    DashScope 要求 response_format=json_object 时 messages 须含 "json" 字样，故把 schema 与 json 指令注入 system。
    """
    llm = get_llm(model)
    instr = (
        "必须只输出一个 JSON 对象，不要输出任何解释文字、代码块或多余内容。"
        "严格按以下 JSON Schema 输出（字段名与类型逐字一致，缺失字段用 null）：\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )

    class _Runnable:
        async def ainvoke(self, messages, config=None, **kwargs):
            if messages and isinstance(messages[0], SystemMessage):
                full = [SystemMessage(messages[0].content + "\n" + instr), *messages[1:]]
            else:
                full = [SystemMessage(instr), *messages]
            resp = await llm.ainvoke(
                full,
                config=config,
                response_format={"type": "json_object"},
                **kwargs,
            )
            return schema.model_validate_json(resp.content)

    return _Runnable()
