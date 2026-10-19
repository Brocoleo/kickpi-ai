"""Configuración del servicio de herramientas."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_", extra="ignore")

    tools_token: str = ""
    log_tail_max: int = 500
    log_bytes_max: int = 64_000
    docker_host: str = "unix:///var/run/docker.sock"


@lru_cache
def get_settings() -> Settings:
    return Settings()
