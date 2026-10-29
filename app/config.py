from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )

    # Urja Meter Ops Upstream Portal
    portal_base_url: str = "https://urja-ops.flockenergy.tech"
    portal_username: str = ""
    portal_password: str = ""

    # Service Configuration
    app_name: str = "Urja Meter Ops API Service"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # Cache & Freshness Settings
    cache_ttl_seconds: int = 300  # 5 minutes for general dataset
    energy_cache_ttl_seconds: int = 60  # 1 minute for volatile consumption data

    # Upstream Request Timeout
    request_timeout_seconds: float = 15.0


@lru_cache()
def get_settings() -> Settings:
    return Settings()
