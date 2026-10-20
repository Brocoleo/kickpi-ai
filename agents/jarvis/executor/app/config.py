"""Configuración del ejecutor multi-host."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_EXECUTOR_", extra="ignore")

    # Token interno que comparte con el agente. Nunca sale del compose.
    token: str = ""

    # Ficheros de configuración (montados de solo lectura).
    hosts_file: str = "/app/config/hosts.yml"
    catalog_file: str = "/app/config/catalog.yml"

    # Directorio con las claves SSH privadas y los known_hosts.
    ssh_dir: str = "/app/ssh"

    # Tiempos (segundos).
    connect_timeout: float = 10.0
    command_timeout: float = 60.0
    status_timeout: float = 8.0

    # Salida maxima que se devuelve al agente (bytes).
    max_output_bytes: int = 64_000

    # Base de datos de auditoria.
    audit_db: str = "/data/audit.db"

    # Numero maximo de reintentos de CONEXION (nunca de una orden ya enviada).
    connect_retries: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
