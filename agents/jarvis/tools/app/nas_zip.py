"""ZIP en streaming para descargar varios archivos del NAS.

Por qué no se escribe un temporal y se sirve: 100 fotos son ~700 MB; un
temporal duplica la escritura en el mismo disco USB (el cuello de botella) y
puede no caber. Aquí `zipfile` escribe en un sumidero que empuja los bytes a
una cola y la respuesta HTTP los va soltando: memoria constante y nada que
borrar después.

Los archivos van en ``ZIP_STORED`` (sin comprimir) a propósito: fotos, vídeos
y PDF ya vienen comprimidos; deflate solo gastaría CPU para ganar <1 %.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import zipfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

log = logging.getLogger("jarvis-tools")

# Si el cliente corta la descarga, el productor debe morir en <1 s. El tope es
# una red de seguridad: nunca se queda un hilo colgado leyendo el disco.
_ESPERA_CANCELACION_S = 5.0


class _Cancelado(Exception):
    """El cliente cortó la descarga: el productor ya no tiene a quién escribir."""


class _Sumidero:
    """File-like de escritura para `zipfile`, sin `seek`.

    `zipfile` detecta que no es buscable y usa descriptores de datos al final
    de cada entrada: exactamente lo que permite escribir un ZIP en streaming.
    """

    def __init__(
        self,
        cola: "queue.Queue[bytes | None]",
        cancelado: threading.Event,
    ) -> None:
        self._cola = cola
        self._cancelado = cancelado

    def write(self, datos: bytes) -> int:
        if self._cancelado.is_set():
            raise _Cancelado
        self._cola.put(bytes(datos))
        return len(datos)

    def flush(self) -> None:
        """`zipfile` llama a flush() al cerrar; aquí no hay buffer propio."""


def _con_sufijo(nombre: str, numero: int) -> str:
    """`foto.jpg` + 2 → `foto (2).jpg` (respeta puntos en el nombre)."""
    tallo, punto, extension = nombre.rpartition(".")
    if not punto or not tallo:
        return f"{nombre} ({numero})"
    return f"{tallo} ({numero}).{extension}"


class ErrorZip(ValueError):
    """Selección inválida. `codigo` es el HTTP que le corresponde."""

    def __init__(self, mensaje: str, codigo: int = 400) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


def preparar(
    relativas: Iterable[str],
    resolver: Callable[[str], Path],
    *,
    max_files: int,
    max_mb: int,
) -> list[Path]:
    """Valida la selección ENTERA antes de emitir el primer byte del ZIP.

    Un fallo a mitad de la descarga dejaría un ZIP roto que el navegador ya
    está guardando; por eso rutas y tamaño se comprueban aquí (400/404/413).
    """
    pedidas = [rel for rel in relativas if rel]
    if not pedidas:
        raise ErrorZip("Selecciona al menos un archivo")
    if len(pedidas) > max_files:
        raise ErrorZip(f"Máximo {max_files} archivos por descarga", 413)

    rutas: list[Path] = []
    vistos: set[Path] = set()
    total = 0
    for rel in pedidas:
        try:
            destino = resolver(rel)
        except ValueError as exc:
            raise ErrorZip(f"Ruta no válida: {rel}") from exc
        except FileNotFoundError as exc:
            raise ErrorZip(f"Archivo no encontrado: {rel}", 404) from exc
        if destino in vistos:
            continue
        vistos.add(destino)
        total += destino.stat().st_size
        rutas.append(destino)

    if total > max_mb * 1024 * 1024:
        raise ErrorZip(
            f"La selección pesa {total // (1024 * 1024)} MB y el tope es "
            f"{max_mb} MB: elige menos archivos",
            413,
        )
    return rutas


def nombres_unicos(rutas: Iterable[Path]) -> list[tuple[str, Path]]:
    """Nombre de cada archivo DENTRO del zip, sin repetidos.

    Dos carpetas pueden tener `IMG_0001.jpg`; el ZIP no admite dos entradas
    con el mismo nombre (el segundo sobrescribiría al primero al extraer).
    """
    usados: set[str] = set()
    salida: list[tuple[str, Path]] = []
    for ruta in rutas:
        nombre = ruta.name
        if nombre in usados:
            numero = 2
            while _con_sufijo(ruta.name, numero) in usados:
                numero += 1
            nombre = _con_sufijo(ruta.name, numero)
        usados.add(nombre)
        salida.append((nombre, ruta))
    return salida


def iter_zip(rutas: Iterable[Path], *, maxsize: int = 8) -> Iterator[bytes]:
    """Genera el ZIP por trozos, leyendo del disco a medida que se envía.

    El productor corre en un hilo porque `zipfile` escribe de forma síncrona
    (sin `await`); la cola acotada evita adelantar la lectura y llenar RAM.
    """
    cola: "queue.Queue[bytes | None]" = queue.Queue(maxsize=maxsize)
    cancelado = threading.Event()
    fallos: list[BaseException] = []

    def productor() -> None:
        try:
            with zipfile.ZipFile(
                _Sumidero(cola, cancelado),
                "w",
                zipfile.ZIP_STORED,
                allowZip64=True,
            ) as zf:
                for nombre, ruta in nombres_unicos(rutas):
                    # `write` lee por bloques: un vídeo de 2 GB no se carga.
                    zf.write(ruta, arcname=nombre)
        except _Cancelado:
            log.info("zip cancelado por el cliente")
        except BaseException as exc:  # noqa: BLE001 - el hilo no debe morir mudo
            fallos.append(exc)
            log.warning("zip interrumpido: %s", exc)
        finally:
            try:
                cola.put(None)
            except Exception:  # pragma: no cover - la cola no lanza nunca
                pass

    hilo = threading.Thread(target=productor, name="nas-zip", daemon=True)
    hilo.start()
    try:
        while True:
            trozo = cola.get()
            if trozo is None:
                break
            yield trozo
    finally:
        # GeneratorExit (cliente desconectado): avisar y vaciar hasta que el
        # productor salga; si no, se quedaría bloqueado en put().
        cancelado.set()
        limite = time.monotonic() + _ESPERA_CANCELACION_S
        while hilo.is_alive() and time.monotonic() < limite:
            try:
                cola.get(timeout=0.1)
            except queue.Empty:
                continue
    if fallos:
        raise fallos[0]
