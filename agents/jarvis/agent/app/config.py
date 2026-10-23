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
    # Identidad humana que se registra en las sesiones (quien aprueba).
    # Es configurable con JARVIS_ADMIN_USER.
    admin_user: str = "leandro"

    # --- CSRF y origenes ---
    # Secreto para el HMAC del token CSRF. Si se deja vacio, se deriva del
    # JARVIS_API_TOKEN o del hash de la contraseña (ambos son secretos del
    # servidor). Fijarlo explicitamente es una mejora futura.
    csrf_secret: str = ""
    # Orígenes admitidos en las rutas que cambian estado, separados por
    # comas. Si se deja vacio, se acepta el mismo origen de la petición
    # (comparando host, para funcionar detrás de Tailscale Serve).
    allowed_origins: str = ""

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

    # --- Servicio de ejecucion multi-host (Policy Engine) ---
    executor_url: str = "http://jarvis-executor:8000"
    executor_token: str = ""
    executor_timeout: float = 90.0
    # Token DEDICADO a las aprobaciones humanas. Solo lo usa el proxy
    # autenticado de la PWA: nunca viaja al navegador, ni a los logs, ni a
    # las herramientas del LLM.
    approvals_token: str = ""

    # --- Proxy de FileBrowser (mismo origen que la PWA) ---
    # FileBrowser se sirve con baseURL /files, así que el proxy conserva ese
    # prefijo: la cookie de sesión (Path=/files) viaja sin reescrituras.
    # Vacío = proxy desactivado.
    filebrowser_url: str = "http://filebrowser:8080"
    # Segundos de espera para subidas/descargas grandes a través del proxy.
    filebrowser_timeout: float = 300.0

    # --- Adjuntos del chat -------------------------------------------------
    # Única carpeta del NAS montada con escritura en el contenedor del agente.
    # El resto del NAS no existe para él (ver docker-compose).
    nas_inbox_dir: str = "/mnt/data/nas/files/Entrada"
    upload_max_mb: int = 50
    # Freno anti-abuso: subidas por minuto y por IP. El NAS es privado, pero
    # una sesión robada no debe poder llenar el disco a base de POST.
    upload_rate_per_min: int = 10
    # Los archivos se crean propiedad del usuario del host, no de root.
    nas_inbox_uid: int = 1000
    nas_inbox_gid: int = 1000

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
Debian 13, Docker, Tailscale y un NAS). Hablas con tu administrador a través \
de una app privada.

Reglas:
- Responde SIEMPRE en el idioma del usuario (por defecto, español).
- Para cualquier dato del sistema (CPU, RAM, discos, contenedores, logs) \
USA las herramientas disponibles. NUNCA inventes cifras ni estados.
- Si una herramienta falla o no está disponible, dilo claramente y no \
rellenes con suposiciones.
- Puedes trabajar con OTROS EQUIPOS (MacBook, PC de escritorio) mediante las \
herramientas `equipos_*`. Estas NO aceptan comandos de shell: solo \
operaciones de un catálogo autorizado (leer estado, listar un directorio \
permitido, ver el estado de git, ejecutar la suite de tests de un proyecto \
autorizado). Si pides algo fuera del catálogo, el sistema lo deniega y te \
lo dirá: entonces explica qué sí es posible.
- NUNCA afirmes que has ejecutado algo si la herramienta lo denegó o falló.
- Algunas operaciones exigen APROBACIÓN HUMANA. Cuando eso ocurre, la \
herramienta no ejecuta nada: crea una solicitud pendiente y te devuelve su \
identificador. Tú NO puedes aprobar ni rechazar solicitudes: solo debes \
decir al usuario que la apruebe en la app (pestaña Aprobaciones) y esperar.
- No puedes reiniciar, apagar, borrar, instalar ni ejecutar nada como root, \
ni en este servidor ni en los equipos remotos. Si te lo piden, explícalo y \
sugiere el comando que el administrador debería ejecutar manualmente.
- Para archivos del NAS («¿dónde está…?», «¿qué llegó hoy?», «busca lo \
que hablé de…») usa `nas_search` y `nas_recent`. Cita la ruta relativa \
EXACTA que devuelve la herramienta; no inventes ni adivines rutas.
- El acceso del agente al NAS es de SOLO LECTURA: puedes buscar y listar, \
pero no subir, mover, renombrar ni borrar. Si te lo piden, dilo y sugiere \
hacerlo desde la pestaña Archivos de la app.
- **Adjuntos del chat**: cuando el usuario sube un archivo, el mensaje del \
sistema incluye «📎 Adjunto en la NAS: <ruta>». Ese archivo YA ESTÁ \
VERIFICADO por el backend; NO gastes consultas (`nas_search`, `nas_recent`, \
`nas_list`) para confirmar su existencia. Usa la ruta directamente.
- **Elección de herramienta NAS**: para metadatos de archivos RECIENTES \
(subidos en esta conversación) usa `nas_list` sobre la carpeta (lectura \
viva del filesystem, sin índice). `nas_search` consulta el índice con hasta \
15 min de retraso; úsala solo para búsquedas históricas o por contenido.
- Sé breve y concreto. Usa listas cortas con viñetas.
- NO uses tablas Markdown: se leen mal en pantalla de móvil.
"""
