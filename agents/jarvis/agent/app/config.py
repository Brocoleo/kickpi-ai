"""Configuración del agente."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_", extra="ignore")

    # --- API pública del agente ---
    api_token: str = ""

    # --- Sesiones de navegador (PWA) ---
    # Hash scrypt de la contraseña de administrador. Si está vacío, el login
    # acepta el propio JARVIS_API_TOKEN. Generar con: python -m app.security
    admin_password_hash: str = ""
    session_ttl_days: int = 30
    cookie_name: str = "jarvis_session"
    cookie_secure: bool = True
    static_dir: str = "/app/static"

    # --- LLM compatible con OpenAI (OpenRouter, OpenAI, Groq...) ---
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    # Modelo principal (puede ser una lista separada por comas).
    llm_model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    # Cadena de reserva, en orden, separada por comas.
    llm_fallback_models: str = (
        "minimax/minimax-m3.1-flash:free,"
        "google/gemma-4-26b-a4b-it:free,"
        "openrouter/free"
    )

    # --- Política de reintentos / fallback ---
    llm_timeout: float = 60.0          # timeout por intento
    llm_total_budget: float = 110.0    # presupuesto total para toda la cadena
    llm_max_tokens: int = 1024
    llm_max_retries_server: int = 1    # reintentos extra solo para 5xx
    llm_retry_backoff: float = 0.8     # segundos (se multiplica por intento)
    llm_circuit_threshold: int = 2     # fallos consecutivos para abrir el circuito
    llm_circuit_cooldown: float = 60.0 # segundos de bloqueo del modelo

    # --- Servicio de herramientas ---
    tools_url: str = "http://jarvis-tools:8000"
    tools_token: str = ""
    tools_timeout: float = 15.0

    # --- Persistencia y límites ---
    data_dir: str = "/data"
    history_limit: int = 20
    max_tool_iterations: int = 4

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def llm_model_chain(self) -> list[str]:
        """Principal + reservas, sin duplicados y conservando el orden."""
        raw = f"{self.llm_model},{self.llm_fallback_models}"
        chain: list[str] = []
        for item in raw.split(","):
            name = item.strip()
            if name and name not in chain:
                chain.append(name)
        return chain


@lru_cache
def get_settings() -> Settings:
    return Settings()


SYSTEM_PROMPT = """\
Eres Jarvis, el asistente de un mini-servidor doméstico (KICKPI K2B V2 con \
Debian 13, Docker, Tailscale y un NAS en preparación). Hablas con tu \
administrador a través de una app privada.

Reglas:
- Responde SIEMPRE en el idioma del usuario (por defecto, español).
- Para cualquier dato del sistema (CPU, RAM, discos, contenedores, logs) \
USA las herramientas disponibles. NUNCA inventes cifras ni estados.
- Si una herramienta falla o no está disponible, dilo claramente y no \
rellenes con suposiciones.
- Solo tienes herramientas de LECTURA. No puedes reiniciar, borrar, crear ni \
modificar nada. Si te lo piden, explícalo y sugiere el comando que el \
administrador debería ejecutar manualmente.
- Sé breve y concreto. Resume en tablas o listas cuando ayude.
"""
