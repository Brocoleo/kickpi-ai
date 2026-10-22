#!/usr/bin/env python3
"""Banco de pruebas del chat de Jarvis contra el servidor REAL.

Mide lo que el usuario siente, no lo que dice un log: cuánto tarda en llegar
el primer evento, el primer token y el cierre; si las herramientas aparecen;
y si la respuesta final llega vacía. Sirve para debuggear "va lento", "se
quedó pensando" o "no hizo nada" sin abrir el contenedor.

Uso (la forma fácil: pregunta y no se ve lo que escribes):

    ./scripts/bench-chat.py
    ./scripts/bench-chat.py --loop 5
    ./scripts/bench-chat.py --comparar --loop 3     # toda la cadena, una vez
    ./scripts/bench-chat.py --estado                # fusible y política del LLM
    ./scripts/bench-chat.py --url http://127.0.0.1:8888
    ./scripts/bench-chat.py --json > medicion.json

Sin `--url` se prueba primero el agente local (127.0.0.1:8888, como dentro de
la placa) y, si nadie escucha ahí, el canal de Tailscale: así el mismo comando
vale desde el Mac y desde un cron de la placa.

Para cron o scripts, la variable de entorno sigue valiendo:

    JARVIS_PASSWORD=... ./scripts/bench-chat.py

La contraseña nunca viaja por argumento (quedaría en el historial del shell)
ni se escribe en un archivo: o sale del entorno, o se pide por el terminal con
`getpass` (como sudo: no se muestra y no pasa por el historial). Solo stdlib.

Salida: tabla por caso y código de salida 1 si algo falla, para poder usarlo
en un cron o en un deploy.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Agente local (cuando el banco corre en la placa) y canal de Tailscale
# (cuando corre desde fuera). Sin `--url` se prueban en este orden.
URL_LOCAL = "http://127.0.0.1:8888"
URL_TAILSCALE = "https://kickpi-jarvis.tail1e3b4d.ts.net"

# Casos por defecto: de solo lectura, para no dejar aprobaciones pendientes.
CASOS = [
    ("simple", "Responde solo con la palabra: listo.", False),
    ("estado", "¿Cómo está el sistema? Resume CPU, memoria y disco en una frase.", True),
    ("contenedores", "¿Qué contenedores están corriendo? Solo los nombres.", True),
]


@dataclass
class Medicion:
    caso: str
    ok: bool = True
    problemas: list[str] = field(default_factory=list)
    ms_primer_evento: int | None = None
    ms_primer_token: int | None = None
    ms_total: int | None = None
    tokens: int = 0
    herramientas: int = 0
    caracteres: int = 0
    # Tokens transmitidos antes de la respuesta final (narración previa a
    # una herramienta). No es un fallo: se informa.
    narracion: int = 0
    # El que se pidió (--model / --comparar) y el que respondió, que deberían
    # coincidir: si no, la medición no vale.
    modelo_pedido: str | None = None
    modelo: str | None = None
    conversacion: str | None = None


class SinConexion(Exception):
    """No hay nada escuchando (o no responde) en la URL indicada.

    Existe para que un fallo de red se cuente en una línea, no como un
    traceback de Python: el banco también va en cron.
    """


def _motivo(exc: Exception) -> str:
    """El motivo de red, en corto (urllib lo esconde en `reason`)."""
    razon = getattr(exc, "reason", exc)
    texto = str(razon) or razon.__class__.__name__
    return texto.splitlines()[-1].strip()


class Cliente:
    def __init__(
        self, base: str, password: str, timeout: float, model: str | None = None
    ) -> None:
        self.base = base.rstrip("/")
        self.password = password
        self.timeout = timeout
        self.model = model
        self.cookie = ""
        self.csrf = ""

    def _cabeceras(self) -> dict[str, str]:
        cabeceras = {
            "Origin": self.base,
            "Content-Type": "application/json",
            "Accept": "*/*",
        }
        if self.cookie:
            cabeceras["Cookie"] = self.cookie
        if self.csrf:
            cabeceras["X-CSRF-Token"] = self.csrf
        return cabeceras

    def login(self) -> None:
        peticion = urllib.request.Request(
            f"{self.base}/api/auth/login",
            data=json.dumps({"password": self.password}).encode(),
            headers=self._cabeceras(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(peticion, timeout=self.timeout) as respuesta:
                cuerpo = json.loads(respuesta.read() or b"{}")
                # La cookie es HttpOnly y Secure; aqui se reenvia a mano
                # porque el banco puede correr contra http://127.0.0.1.
                for valor in respuesta.headers.get_all("Set-Cookie") or []:
                    par = valor.split(";", 1)[0]
                    if "=" in par:
                        self.cookie = par
        except urllib.error.HTTPError as exc:
            detalle = exc.read().decode(errors="replace")[:200]
            raise SystemExit(f"login falló ({exc.code}): {detalle}") from exc
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            raise SinConexion(f"{self.base} ({_motivo(exc)})") from None

        self.csrf = cuerpo.get("csrf_token") or ""
        if not self.cookie:
            raise SystemExit("login no devolvió cookie de sesión")

    def estado_llm(self) -> dict:
        """Cadena, fusible y política del servidor (para saber qué acepta --model).

        Es informativo: si el servidor es viejo y no tiene la ruta, se
        devuelve un diccionario vacío y el banco sigue como siempre.
        """
        peticion = urllib.request.Request(
            f"{self.base}/api/llm/status", headers=self._cabeceras()
        )
        try:
            with urllib.request.urlopen(peticion, timeout=self.timeout) as respuesta:
                return json.loads(respuesta.read() or b"{}")
        except (urllib.error.HTTPError, OSError, ValueError):
            return {}

    def borrar_conversacion(self, conv_id: str) -> bool:
        peticion = urllib.request.Request(
            f"{self.base}/api/conversations/{conv_id}",
            headers=self._cabeceras(),
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(peticion, timeout=self.timeout):
                return True
        except urllib.error.HTTPError:
            return False

    def medir(self, caso: str, mensaje: str, espera_herramientas: bool) -> Medicion:
        m = Medicion(caso=caso)
        inicio = time.monotonic()
        cuerpo: dict = {"message": mensaje, "conversation_id": None}
        if self.model:
            cuerpo["model"] = self.model
        peticion = urllib.request.Request(
            f"{self.base}/api/chat/stream",
            data=json.dumps(cuerpo).encode(),
            headers=self._cabeceras(),
            method="POST",
        )

        try:
            respuesta = urllib.request.urlopen(peticion, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detalle = exc.read().decode(errors="replace")[:200]
            m.ok = False
            m.problemas.append(f"HTTP {exc.code}: {detalle}")
            return m
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            m.ok = False
            m.ms_total = int((time.monotonic() - inicio) * 1000)
            m.problemas.append(f"no se pudo conectar: {_motivo(exc)}")
            return m

        evento = ""
        segmentos = 0
        texto = ""
        cierre: dict = {}

        with respuesta:
            for linea_cruda in respuesta:
                linea = linea_cruda.decode(errors="replace").strip()
                if linea.startswith("event:"):
                    evento = linea[6:].strip()
                    continue
                if not linea.startswith("data:"):
                    continue
                datos = json.loads(linea[5:].strip() or "{}")

                if m.ms_primer_evento is None:
                    m.ms_primer_evento = int((time.monotonic() - inicio) * 1000)

                if evento == "segment":
                    segmentos += 1
                elif evento == "token":
                    texto += datos.get("text", "")
                    m.tokens += 1
                    if m.ms_primer_token is None:
                        m.ms_primer_token = int((time.monotonic() - inicio) * 1000)
                elif evento == "tool":
                    m.herramientas += 1
                elif evento == "done":
                    cierre = datos
                elif evento == "error":
                    m.ok = False
                    m.problemas.append(f"error del servidor: {datos.get('detail')}")

        m.ms_total = int((time.monotonic() - inicio) * 1000)
        m.caracteres = len(texto)
        m.modelo = cierre.get("model")
        m.conversacion = cierre.get("conversation_id")

        # Diagnóstico del propio servidor (desplegado desde 0.7): sirve para
        # contrastar la medición del cliente con la del servidor.
        if not cierre:
            m.ok = False
            m.problemas.append("no llegó el evento done")
        if segmentos == 0:
            m.ok = False
            m.problemas.append("no hubo segment (la burbuja nunca se abrió)")
        if m.tokens == 0:
            m.ok = False
            m.problemas.append("no llegó ni un token")
        if cierre:
            respuesta_final = (cierre.get("reply") or "").strip()
            transmitido = texto.strip()
            if not respuesta_final:
                m.ok = False
                m.problemas.append("respuesta final vacía")
            elif transmitido:
                # El cierre lleva SOLO el texto final del turno. Si el modelo
                # habló antes de llamar a una herramienta, esos tokens se
                # transmitieron y se guardan como mensaje aparte: lo correcto
                # es que la respuesta final CIERRE el stream. La otra
                # dirección (el stream cabe en la respuesta) es el nodo de
                # límite, que añade texto que no pasó por el stream.
                if transmitido.endswith(respuesta_final):
                    m.narracion = len(transmitido) - len(respuesta_final)
                elif transmitido in respuesta_final:
                    pass
                else:
                    m.ok = False
                    m.problemas.append(
                        "el stream y la respuesta persistida no coinciden "
                        f"({len(transmitido)} vs {len(respuesta_final)} caracteres)"
                    )
        if espera_herramientas and m.herramientas == 0:
            m.ok = False
            m.problemas.append("no usó ninguna herramienta (se esperaba al menos una)")
        if not espera_herramientas and m.herramientas > 0 and caso == "simple":
            m.problemas.append("usó herramientas en un caso que no las necesitaba")

        return m


def obtener_password() -> str:
    """La contraseña del entorno o, si falta, preguntada por el terminal.

    `getpass` lee de /dev/tty, así que no se ve lo tecleado y no queda en el
    historial del shell. En un cron no hay terminal: ahí se exige la variable
    de entorno y se falla claro, sin quedarse esperando una entrada que nunca
    llegará.
    """
    valor = os.environ.get("JARVIS_PASSWORD", "")
    if valor:
        return valor

    if not sys.stdin.isatty():
        return ""

    try:
        return getpass.getpass(f"Contraseña de {os.environ.get('JARVIS_URL', 'Jarvis')}: ")
    except (KeyboardInterrupt, EOFError):
        print("", file=sys.stderr)
        return ""


def percentil(valores: list[int], p: float) -> int:
    if not valores:
        return 0
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, max(0, round((p / 100) * (len(ordenados) - 1))))
    return ordenados[indice]


def mostrar_estado(base: str, estado: dict) -> None:
    """La cadena, el fusible y la política: el porqué de un fallo, sin sudo.

    Cuando un modelo se queda ciego, esto distingue las dos causas posibles:
    fusible abierto por fallos seguidos (se cura solo) o el proveedor
    rechazando (por ejemplo, cuota diaria del nivel gratuito).
    """
    if not estado:
        print(f"{base} no informa su estado LLM (¿agente viejo?)")
        return

    print(f"servidor: {base}")
    print("modelos (en orden de intento):")
    for numero, modelo in enumerate(estado.get("models") or [], 1):
        print(f"  {numero}. {modelo}")

    fusible = estado.get("circuit_breaker") or {}
    print("fusible:")
    if not fusible:
        print("  nada abierto: todos respondieron o aún no se probaron")
    for modelo, datos in fusible.items():
        estado_modelo = "ABIERTO" if datos.get("open") else "half-open (ya se reintenta)"
        print(
            f"  {modelo}: {estado_modelo}, reabre en {datos.get('reopens_in_s')}s, "
            f"fallos {datos.get('failures')}, último: {datos.get('last_error')}"
        )

    politica = estado.get("policy") or {}
    if politica:
        print("política:")
        for clave, valor in politica.items():
            print(f"  {clave}: {valor}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Banco de pruebas del chat real")
    parser.add_argument(
        "--url",
        default=None,
        help=(
            "base del agente. Por defecto el agente local y, si ahí no "
            "responde nadie, el canal de Tailscale"
        ),
    )
    parser.add_argument("--loop", type=int, default=1, help="repeticiones por caso")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--casos",
        default="simple,estado,contenedores",
        help="lista separada por comas: simple,estado,contenedores",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "fuerza un modelo de la cadena configurada (sin fallback). "
            "Para comparar latencias sin tocar el .env"
        ),
    )
    parser.add_argument(
        "--comparar",
        action="store_true",
        help=(
            "mide todos los modelos de la cadena, uno tras otro, y saca la "
            "tabla comparativa. Una sola contraseña para toda la cadena"
        ),
    )
    parser.add_argument(
        "--estado",
        action="store_true",
        help=(
            "muestra la cadena de modelos, el fusible (circuit breaker) y la "
            "política de fallback del servidor, y no mide nada"
        ),
    )
    parser.add_argument("--json", action="store_true", help="salida JSON")
    parser.add_argument(
        "--no-cleanup", action="store_true", help="no borrar las conversaciones creadas"
    )
    args = parser.parse_args()

    password = obtener_password()
    if not password:
        print(
            "falta la contraseña. Sin terminal interactivo hay que pasarla por "
            "entorno (nunca por argumento):\n"
            "  JARVIS_PASSWORD=... ./scripts/bench-chat.py",
            file=sys.stderr,
        )
        return 2

    pedidos = [c.strip() for c in args.casos.split(",") if c.strip()]
    seleccion = [c for c in CASOS if c[0] in pedidos]
    if not seleccion:
        print(f"no hay casos válidos en {pedidos}", file=sys.stderr)
        return 2

    # Sin `--url` (ni JARVIS_URL) se prueba el agente local y, si no hay nada
    # escuchando, el canal de Tailscale: el mismo comando sirve en el Mac y en
    # la placa.
    base = args.url or os.environ.get("JARVIS_URL") or URL_LOCAL
    cliente = Cliente(base, password, args.timeout, args.model)
    try:
        cliente.login()
    except SinConexion as exc:
        if base != URL_LOCAL:
            print(f"no pude conectar: {exc}", file=sys.stderr)
            return 2
        print(f"sin respuesta en {exc}; pruebo {URL_TAILSCALE}", file=sys.stderr)
        cliente = Cliente(URL_TAILSCALE, password, args.timeout, args.model)
        try:
            cliente.login()
        except SinConexion as exc2:
            print(f"no pude conectar: {exc2}", file=sys.stderr)
            return 2

    # Solo se pueden medir modelos de la cadena configurada. Enseñarla
    # evita adivinar nombres y chocar con el 400 del servidor.
    estado_llm = cliente.estado_llm()
    cadena = list(estado_llm.get("models") or [])

    if args.estado:
        if args.json:
            print(json.dumps(estado_llm, indent=2, ensure_ascii=False))
        else:
            mostrar_estado(cliente.base, estado_llm)
        return 0

    if cadena and not args.json and not args.comparar:
        print("modelos de la cadena: " + ", ".join(cadena))
    if args.model and cadena and args.model not in cadena:
        print(
            f"«{args.model}» no está en la cadena del servidor. Elige uno de:\n  "
            + "\n  ".join(cadena),
            file=sys.stderr,
        )
        return 2

    # Qué se mide: un modelo forzado, todos los de la cadena (--comparar) o
    # la cadena normal con su fallback (None). En los dos primeros casos el
    # modelo es el que responde o la medición falla; en el tercero, manda la
    # política de reserva del agente.
    if args.comparar:
        if not cadena:
            print(
                "el servidor no informa su cadena: usa --model para medir de uno "
                "en uno",
                file=sys.stderr,
            )
            return 2
        objetivos: list[str | None] = list(cadena)
    elif args.model:
        objetivos = [args.model]
    else:
        objetivos = [None]

    mediciones: list[Medicion] = []
    for objetivo in objetivos:
        cliente.model = objetivo
        for _ in range(max(1, args.loop)):
            for caso, mensaje, espera in seleccion:
                m = cliente.medir(caso, mensaje, espera)
                m.modelo_pedido = objetivo
                # Con modelo fijado, la medición es mentira si el servidor usó
                # otro (por ejemplo, si el fallback se activó a espaldas del
                # banco).
                if objetivo and m.modelo and m.modelo != objetivo:
                    m.ok = False
                    m.problemas.append(f"usó {m.modelo} en vez de {objetivo}")
                mediciones.append(m)
                if m.conversacion and not args.no_cleanup:
                    # El banco no deja rastro en el historial del usuario.
                    cliente.borrar_conversacion(m.conversacion)
                if args.comparar and not args.json:
                    # Una línea por medición: la comparación tarda minutos y
                    # conviene ver que avanza. Si falla, dice por qué: sin eso
                    # haber sabido la causa exige un diagnóstico aparte.
                    detalle = "" if m.ok else f" → {'; '.join(m.problemas)}"
                    print(
                        f"  {objetivo} · {caso}: "
                        f"{'ok' if m.ok else 'FALLO'} ({m.ms_total or 0}ms){detalle}",
                        file=sys.stderr,
                    )

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "caso": m.caso,
                        "ok": m.ok,
                        "problemas": m.problemas,
                        "ms_primer_evento": m.ms_primer_evento,
                        "ms_primer_token": m.ms_primer_token,
                        "ms_total": m.ms_total,
                        "tokens": m.tokens,
                        "herramientas": m.herramientas,
                        "caracteres": m.caracteres,
                        "narracion": m.narracion,
                        "modelo_pedido": m.modelo_pedido,
                        "modelo": m.modelo,
                    }
                    for m in mediciones
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.comparar:
        # Una fila por modelo y caso: es la tabla con la que se decide.
        ancho = max(len(o or "cadena") for o in objetivos)
        print()
        print(
            f"{'modelo':<{ancho}}  {'caso':<14} {'1er tok p50':>11} "
            f"{'total p50':>9} {'fallos':>6}"
        )
        print("-" * (ancho + 46))
        for objetivo in objetivos:
            for caso, _, _ in seleccion:
                grupo = [
                    m
                    for m in mediciones
                    if m.modelo_pedido == objetivo and m.caso == caso
                ]
                tokens = [m.ms_primer_token for m in grupo if m.ms_primer_token]
                totales = [m.ms_total for m in grupo if m.ms_total]
                fallos = sum(1 for m in grupo if not m.ok)
                print(
                    f"{(objetivo or 'cadena'):<{ancho}}  {caso:<14} "
                    f"{percentil(tokens, 50):>8}ms {percentil(totales, 50):>6}ms "
                    f"{fallos:>4}/{len(grupo)}"
                )
            print()

        narradores = [m for m in mediciones if m.narracion]
        if narradores:
            print(
                f"{len(narradores)} mediciones narraron antes de usar una "
                "herramienta (no es un fallo)"
            )

        # El más veloz, para no tener que leer la tabla entera.
        ranking: list[tuple[int, str]] = []
        for objetivo in objetivos:
            grupo = [m for m in mediciones if m.modelo_pedido == objetivo and m.ok]
            tokens = [m.ms_primer_token for m in grupo if m.ms_primer_token]
            if tokens:
                ranking.append((percentil(tokens, 50), objetivo or "cadena"))
        if ranking:
            ranking.sort()
            print(
                f"\nmás rápido al primer token: {ranking[0][1]} "
                f"({ranking[0][0]}ms p50)"
            )
    else:
        if args.model:
            print(f"modelo forzado: {args.model}\n")
        print(
            f"{'caso':<14} {'1er ev.':>8} {'1er tok.':>9} {'total':>8} "
            f"{'tok':>4} {'herr':>5} {'chars':>6}  resultado"
        )
        print("-" * 84)
        for m in mediciones:
            marca = "ok" if m.ok else "FALLO"
            detalle = "" if m.ok else f" ({'; '.join(m.problemas)})"
            print(
                f"{m.caso:<14} {m.ms_primer_evento or 0:>7}ms "
                f"{m.ms_primer_token or 0:>8}ms {m.ms_total or 0:>7}ms "
                f"{m.tokens:>4} {m.herramientas:>5} {m.caracteres:>6}  {marca}{detalle}"
            )

        narradores = [m for m in mediciones if m.narracion]
        if narradores:
            print()
            print(
                f"{len(narradores)} turno(s) hablaron antes de usar una "
                "herramienta (no es un fallo; se guarda como mensaje aparte):"
            )
            for m in narradores:
                print(f"  {m.caso}: {m.narracion} caracteres antes de la respuesta final")

        if args.loop > 1:
            print()
            for caso, _, _ in seleccion:
                grupo = [m for m in mediciones if m.caso == caso]
                primer_token = [m.ms_primer_token for m in grupo if m.ms_primer_token]
                total = [m.ms_total for m in grupo if m.ms_total]
                print(
                    f"{caso:<14} p50 1er token {percentil(primer_token, 50):>6}ms  "
                    f"p95 {percentil(primer_token, 95):>6}ms   "
                    f"p50 total {percentil(total, 50):>6}ms  "
                    f"p95 {percentil(total, 95):>6}ms"
                )

    fallos = [m for m in mediciones if not m.ok]
    if fallos:
        print(f"\n{len(fallos)} de {len(mediciones)} mediciones fallaron", file=sys.stderr)
        return 1
    if not args.json:
        print(f"\ntodo correcto ({len(mediciones)} mediciones)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
