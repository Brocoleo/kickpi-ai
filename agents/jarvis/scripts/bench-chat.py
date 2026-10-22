#!/usr/bin/env python3
"""Banco de pruebas del chat de Jarvis contra el servidor REAL.

Mide lo que el usuario siente, no lo que dice un log: cuánto tarda en llegar
el primer evento, el primer token y el cierre; si las herramientas aparecen;
y si la respuesta final llega vacía. Sirve para debuggear "va lento", "se
quedó pensando" o "no hizo nada" sin abrir el contenedor.

Uso (la forma fácil: pregunta y no se ve lo que escribes):

    ./scripts/bench-chat.py
    ./scripts/bench-chat.py --loop 5
    ./scripts/bench-chat.py --url http://127.0.0.1:8888
    ./scripts/bench-chat.py --json > medicion.json

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
    modelo: str | None = None
    conversacion: str | None = None


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

        self.csrf = cuerpo.get("csrf_token") or ""
        if not self.cookie:
            raise SystemExit("login no devolvió cookie de sesión")

    def modelos(self) -> list[str]:
        """Cadena configurada en el servidor (para saber qué acepta --model).

        Es informativo: si el servidor es viejo y no tiene la ruta, se
        devuelve una lista vacía y el banco sigue como siempre.
        """
        peticion = urllib.request.Request(
            f"{self.base}/api/llm/status", headers=self._cabeceras()
        )
        try:
            with urllib.request.urlopen(peticion, timeout=self.timeout) as respuesta:
                return list(json.loads(respuesta.read() or b"{}").get("models") or [])
        except (urllib.error.HTTPError, OSError, ValueError):
            return []

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Banco de pruebas del chat real")
    parser.add_argument(
        "--url",
        default=os.environ.get("JARVIS_URL", "http://127.0.0.1:8888"),
        help="base del agente (por defecto la placa en local)",
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

    cliente = Cliente(args.url, password, args.timeout, args.model)
    cliente.login()

    # Solo se pueden medir modelos de la cadena configurada. Enseñarla
    # evita adivinar nombres y chocar con el 400 del servidor.
    cadena = cliente.modelos()
    if cadena and not args.json:
        print("modelos de la cadena: " + ", ".join(cadena))
    if args.model and cadena and args.model not in cadena:
        print(
            f"«{args.model}» no está en la cadena del servidor. Elige uno de:\n  "
            + "\n  ".join(cadena),
            file=sys.stderr,
        )
        return 2

    mediciones: list[Medicion] = []
    for _ in range(max(1, args.loop)):
        for caso, mensaje, espera in seleccion:
            m = cliente.medir(caso, mensaje, espera)
            # Con modelo forzado, la medición es mentira si el servidor usó
            # otro (por ejemplo, si el fallback se activó a espaldas del banco).
            if args.model and m.modelo and m.modelo != args.model:
                m.ok = False
                m.problemas.append(f"usó {m.modelo} en vez de {args.model}")
            mediciones.append(m)
            if m.conversacion and not args.no_cleanup:
                # El banco no deja rastro en el historial del usuario.
                cliente.borrar_conversacion(m.conversacion)

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
                        "modelo": m.modelo,
                    }
                    for m in mediciones
                ],
                indent=2,
                ensure_ascii=False,
            )
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
