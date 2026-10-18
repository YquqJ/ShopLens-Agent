"""应用配置：pydantic-settings 统一读取 .env。

设计决策：
- 用 BaseSettings 集中管理配置并做类型校验，避免 os.getenv 散读；
- DATABASE_URL 默认指向 docker-compose 起的本地 Postgres；
- CHROMA_DIR 控制向量持久化目录，相对路径统一相对 backend 根解析，
  保证无论从哪里启动（backend/ 或项目根）落盘位置一致。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 根目录：本文件位于 backend/app/core/config.py，向上两级
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # 项目根 .env（与 .env.example 同位置），不随 CWD 变化
        env_file=str(BASE_DIR.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DashScope（Qwen）OpenAI 兼容接入
    DASHSCOPE_API_KEY: str = ""

    # DashScope OpenAI 兼容接口地址（embedding / LLM 共用）
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # Postgres 异步连接串（psycopg）
    DATABASE_URL: str = "postgresql+psycopg://shoplens:shoplens@localhost:5432/shoplens"

    # Chroma 向量持久化目录（本地文件，无独立容器）
    CHROMA_DIR: str = "chroma"

    # 值得买海纳开放平台 apikey（官方 Streamable HTTP MCP 真实商品检索源）
    ZDM_API_KEY: str = ""

    # 是否启用真实商品检索源；置 1 后检索层直连海纳，跳过比价/下单闭环
    SHOPLENS_ZHIDEMAI_ENABLED: bool = False

    # Langfuse 可选观测（默认关闭；不影响本地离线运行）
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_BASE_URL: str = "https://cloud.langfuse.com"
    LANGFUSE_TRACING_ENVIRONMENT: str = "development"
    LANGFUSE_RELEASE: str = ""
    LANGFUSE_SAMPLE_RATE: float = 1.0

    @property
    def chroma_path(self) -> Path:
        """CHROMA_DIR 相对 backend 根解析为绝对路径。"""
        p = Path(self.CHROMA_DIR)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def postgres_dsn(self) -> str:
        """psycopg 原生 DSN（langgraph-checkpoint-postgres 用），去掉 SQLAlchemy 方言前缀。"""
        return self.DATABASE_URL.replace("+psycopg", "", 1)


settings = Settings()
