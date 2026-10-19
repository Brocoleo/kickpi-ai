"""Configuración del servicio de herramientas."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_", extra="ignore")

    tools_token: str = ""
    log_tail_max: int = 500
    log_bytes_max: int = 64_000
    # El socket local ya no se monta en este servicio: siempre el proxy.
    docker_host: str = "tcp://docker-socket-proxy:2375"
    # Versión de la API de Docker. Vacío = valor por defecto (1.45).
    docker_api_version: str = "1.45"


@lru_cache
def get_settings() -> Settings:
    return Settings()
