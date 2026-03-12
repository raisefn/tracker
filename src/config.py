import os

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://tracker:tracker@localhost:5432/tracker"
    redis_url: str = "redis://localhost:6379/0"
    api_prefix: str = "/v1"
    debug: bool = False
    collector_batch_size: int = 500
    default_page_limit: int = 50
    max_page_limit: int = 200
    min_confidence: float = 0.3
    coingecko_api_key: str = ""
    github_token: str = ""
    etherscan_api_key: str = ""
    cors_origins: list[str] = ["*"]

    @model_validator(mode="before")
    @classmethod
    def map_platform_urls(cls, values):
        """Fall back to DATABASE_URL / REDIS_URL if RAISEFN_ prefixed vars aren't set.

        Railway (and other PaaS) inject these automatically from add-ons.
        Also converts postgresql:// to postgresql+asyncpg:// for SQLAlchemy async.
        """
        if not values.get("database_url") and not os.environ.get("RAISEFN_DATABASE_URL"):
            raw = os.environ.get("DATABASE_URL", "")
            if raw:
                values["database_url"] = raw.replace(
                    "postgresql://", "postgresql+asyncpg://", 1
                )
        if not values.get("redis_url") and not os.environ.get("RAISEFN_REDIS_URL"):
            raw = os.environ.get("REDIS_URL", "")
            if raw:
                values["redis_url"] = raw
        return values

    model_config = {"env_prefix": "RAISEFN_", "env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
