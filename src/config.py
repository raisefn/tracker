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

    model_config = {"env_prefix": "RAISEFN_"}


settings = Settings()
