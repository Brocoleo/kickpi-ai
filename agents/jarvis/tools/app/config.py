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
    # NAS montada en solo lectura dentro del contenedor (ver docker-compose).
    nas_root: str = "/mnt/data/nas"
    nas_refresh_s: float = 900.0
    nas_max_files: int = 300_000
    # Caché del índice: sobrevive a reinicios del contenedor. Si el volumen
    # no está montado, el índice sigue funcionando (solo tarda en arrancar).
    nas_index_cache: str = "/data/nas-index.json"
    # Tope para servir un archivo por la API (miniaturas y descargas).
    nas_file_max_mb: int = 25
    # Descarga en lote: 100 fotos de un móvil son ~700 MB y 100 entradas.
    nas_zip_max_files: int = 200
    nas_zip_max_mb: int = 2048


@lru_cache
def get_settings() -> Settings:
    return Settings()
