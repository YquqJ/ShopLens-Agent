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
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DashScope（Qwen）OpenAI 兼容接入
    DASHSCOPE_API_KEY: str = ""

    # Postgres 异步连接串（psycopg）
    DATABASE_URL: str = "postgresql+psycopg://shoplens:shoplens@localhost:5432/shoplens"

    # Chroma 向量持久化目录（本地文件，无独立容器）
    CHROMA_DIR: str = "chroma"

    @property
    def chroma_path(self) -> Path:
        """CHROMA_DIR 相对 backend 根解析为绝对路径。"""
        p = Path(self.CHROMA_DIR)
        return p if p.is_absolute() else BASE_DIR / p


settings = Settings()