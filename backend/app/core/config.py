"""集中配置：环境变量 > .env > 默认值。所有模块只能从这里读配置，禁止散落 os.getenv。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 运行环境
    app_env: str = "dev"
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/procurement"
    db_connect_timeout_s: float = 1.5
    fx_usd_cny: float = 7.1

    # 事件总线
    sse_poll_interval_ms: int = 250
    sse_heartbeat_s: int = 15
    demo_step_delay_ms: int = 350

    # LLM 网关
    force_fake_llm: bool = True
    llm_config_path: str = "app/llm/models.yaml"
    prompt_cache_dir: str = ".cache/prompts"

    # 演示限额
    demo_rate_limit_runs: int = 10

    # M2 价格评分：账期资金占用折算（0=关闭）
    m2_financing_annual_rate: float = 0.08
    m2_financing_baseline_days: float = 30.0

    @property
    def llm_config_file(self) -> Path:
        p = Path(self.llm_config_path)
        return p if p.is_absolute() else (BACKEND_ROOT / p)

    @property
    def cache_dir(self) -> Path:
        p = Path(self.prompt_cache_dir)
        return p if p.is_absolute() else (BACKEND_ROOT / p)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
