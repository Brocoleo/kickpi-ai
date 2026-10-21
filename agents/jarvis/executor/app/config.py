"""Configuración del ejecutor multi-host."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_EXECUTOR_", extra="ignore")

    # Token interno que comparte con el agente. Nunca sale del compose.
    token: str = ""

    # Token de APROBACIONES (Fase C). Deliberadamente DISTINTO del token del
    # ejecutor: el agente lo usa solo como proxy autenticado de la PWA y
    # NUNCA lo inyecta en el flujo de herramientas del LLM.
    approvals_token: str = ""

    # Ficheros de configuración (montados de solo lectura).
    hosts_file: str = "/app/config/hosts.yml"
    catalog_file: str = "/app/config/catalog.yml"

    # Directorio con las claves SSH privadas y los known_hosts.
    ssh_dir: str = "/app/ssh"

    # Tiempos (segundos).
    connect_timeout: float = 10.0
    command_timeout: float = 60.0
    status_timeout: float = 8.0
    probe_timeout: float = 3.0

    # Salida maxima que se devuelve al agente (bytes).
    max_output_bytes: int = 64_000

    # Base de datos de auditoria.
    audit_db: str = "/data/audit.db"

    # --- Aprobaciones humanas (Fase C) ---
    # Vida maxima de una solicitud pendiente/aprobada (5 minutos).
    approval_ttl_seconds: int = 300
    # Maximo de solicitudes pendientes por host (anti-flood del LLM).
    max_pending_per_host: int = 20
    # A partir de este tiempo en estado 'executing', el resultado se considera
    # incierto: la solicitud se marca 'failed' y NO se reintenta nunca.
    stale_executing_seconds: int = 600

    # Numero maximo de reintentos de CONEXION (nunca de una orden ya enviada).
    connect_retries: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
