"""Índice en memoria de la NAS para buscar desde el chat.

El servicio de herramientas es de solo lectura y no depende de `find` ni de
`grep`: recorre la raíz una vez, guarda nombre/ruta/tamaño/fecha y responde
consultas al instante. El contenido de los archivos de texto se busca bajo
demanda, con presupuesto de tiempo, para no recorrer 600 GB por pregunta.

Decisiones:
- Se ignoran directorios de sistema o ruido (`.git`, `node_modules`,
  `@eaDir`, papelera de Synology…) y los ocultos.
- La búsqueda es insensible a mayúsculas y acentos: «reunión» encuentra
  «reunion» y al revés.
- Tope de entradas (`nas_max_files`) para que un disco enorme no se coma la
  RAM del contenedor: si se alcanza, `truncated=true` lo dice.
- El índice se refresca en segundo plano cada `nas_refresh_s`; una consulta
  nunca espera a un reescaneo completo si ya hay uno disponible.
"""
from __future__ import annotations

import os
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Ruido conocido en NAS domésticas y repos.
_SKIP_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "@eadir",
    "#recycle",
    "$recycle.bin",
    ".trash",
    ".trashes",
    ".stversions",
    ".cache",
    "lost+found",
}

# Solo se busca dentro de archivos que se pueden leer como texto.
_TEXT_EXT = {
    ".md",
    ".txt",
    ".log",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".ini",
    ".conf",
    ".toml",
    ".py",
    ".sh",
    ".html",
    ".xml",
    ".sql",
    ".rst",
    ".env",
}


def normalizar(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar «reunión» con «reunion»."""
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c)).lower()


@dataclass(frozen=True)
class Entrada:
    ruta: str  # relativa a la raíz, con "/"
    nombre: str
    tamano: int
    mtime: float

    @property
    def modificado(self) -> str:
        return datetime.fromtimestamp(self.mtime, tz=timezone.utc).isoformat()


class IndiceNAS:
    """Índice perezoso y refrescable. Seguro para varios hilos."""

    def __init__(
        self,
        raiz: str | Path,
        *,
        max_files: int = 300_000,
        refresh_s: float = 900.0,
    ) -> None:
        self.raiz = Path(raiz)
        self.max_files = max_files
        self.refresh_s = refresh_s
        self._lock = threading.Lock()
        self._entradas: list[Entrada] = []
        self._construido_en = 0.0
        self._construyendo = False
        self._truncado = False
        self._error: str | None = None

    # ------------------------------------------------------------------ #
    # Construcción
    # ------------------------------------------------------------------ #
    def _recorrer(self) -> tuple[list[Entrada], bool, str | None]:
        entradas: list[Entrada] = []
        truncado = False
        error: str | None = None

        if not self.raiz.is_dir():
            return entradas, False, f"la raíz no existe o no es un directorio: {self.raiz}"

        pendientes = [self.raiz]
        while pendientes and len(entradas) < self.max_files:
            actual = pendientes.pop()
            try:
                iterator = os.scandir(actual)
            except OSError:
                continue
            with iterator:
                for item in iterator:
                    try:
                        if item.is_dir(follow_symlinks=False):
                            nombre = item.name
                            if nombre.startswith(".") or nombre.lower() in _SKIP_DIRS:
                                continue
                            pendientes.append(Path(item.path))
                        elif item.is_file(follow_symlinks=False):
                            stat = item.stat(follow_symlinks=False)
                            rel = os.path.relpath(item.path, self.raiz).replace(os.sep, "/")
                            entradas.append(
                                Entrada(
                                    ruta=rel,
                                    nombre=item.name,
                                    tamano=stat.st_size,
                                    mtime=stat.st_mtime,
                                )
                            )
                    except OSError:
                        # Un archivo que desaparece a mitad del recorrido no
                        # debe tumbar el índice entero.
                        continue

        truncado = len(entradas) >= self.max_files
        entradas.sort(key=lambda e: e.mtime, reverse=True)
        return entradas, truncado, error

    def _construir(self) -> None:
        try:
            entradas, truncado, error = self._recorrer()
        except Exception as exc:  # noqa: BLE001 - el índice nunca tumba el servicio
            entradas, truncado, error = [], False, f"error indexando: {exc}"
        with self._lock:
            self._entradas = entradas
            self._truncado = truncado
            self._error = error
            self._construido_en = time.time()
            self._construyendo = False

    def asegurar(self, espera_s: float = 5.0) -> bool:
        """Garantiza un índice (aunque sea viejo) y lanza refresco si toca.

        Devuelve True si hay algo que consultar. Nunca bloquea más de
        `espera_s`: si el primer recorrido tarda (disco grande), la consulta
        responde «indexando» en vez de colgar el turno del modelo.
        """
        with self._lock:
            fresco = bool(self._entradas) and (
                time.time() - self._construido_en
            ) < self.refresh_s
            if fresco:
                return True
            if not self._construyendo:
                self._construyendo = True
                threading.Thread(
                    target=self._construir, name="nas-index", daemon=True
                ).start()

        limite = time.time() + max(0.0, espera_s)
        while time.time() < limite:
            with self._lock:
                if self._entradas or not self._construyendo:
                    return bool(self._entradas)
            time.sleep(0.05)

        with self._lock:
            return bool(self._entradas)

    # ------------------------------------------------------------------ #
    # Consultas
    # ------------------------------------------------------------------ #
    def _tokens(self, consulta: str) -> list[str]:
        tokens = [normalizar(t) for t in consulta.split() if t.strip()]
        # Una sola palabra de 1 carácter no filtra nada útil.
        return [t for t in tokens if len(t) >= 2] or [normalizar(consulta)]

    def buscar(
        self,
        consulta: str,
        *,
        limite: int = 10,
        contenido: bool = True,
        max_archivos_texto: int = 200,
        presupuesto_s: float = 2.0,
        max_bytes_texto: int = 256_000,
    ) -> dict:
        listo = self.asegurar(5.0)
        with self._lock:
            entradas = self._entradas
            truncado = self._truncado
            error = self._error

        tokens = self._tokens(consulta)
        frase = normalizar(consulta).strip()
        resultados: list[dict] = []
        ya = set()

        for entrada in entradas:
            nombre = normalizar(entrada.nombre)
            ruta = normalizar(entrada.ruta)
            if not all(token in ruta for token in tokens):
                continue
            puntos = 1
            if all(token in nombre for token in tokens):
                puntos += 2
            if frase and frase in nombre:
                puntos += 4
            resultados.append(
                {
                    "path": entrada.ruta,
                    "name": entrada.nombre,
                    "size": entrada.tamano,
                    "modified": entrada.modificado,
                    "match": "nombre" if all(t in nombre for t in tokens) else "ruta",
                    "score": puntos,
                    "_mtime": entrada.mtime,
                }
            )
            ya.add(entrada.ruta)

        contenido_buscado = 0
        if contenido and listo:
            fin = time.monotonic() + presupuesto_s
            for entrada in entradas:
                if contenido_buscado >= max_archivos_texto or time.monotonic() > fin:
                    break
                if entrada.ruta in ya:
                    continue
                if entrada.tamano > max_bytes_texto:
                    continue
                if Path(entrada.nombre).suffix.lower() not in _TEXT_EXT:
                    continue
                contenido_buscado += 1
                try:
                    crudo = (self.raiz / entrada.ruta).read_bytes()
                except OSError:
                    continue
                texto = crudo.decode("utf-8", errors="ignore")
                plano = normalizar(texto)
                if not all(token in plano for token in tokens):
                    continue
                resultados.append(
                    {
                        "path": entrada.ruta,
                        "name": entrada.nombre,
                        "size": entrada.tamano,
                        "modified": entrada.modificado,
                        "match": "contenido",
                        "snippet": _recorte(texto, tokens),
                        "score": 0,
                        "_mtime": entrada.mtime,
                    }
                )

        resultados.sort(key=lambda r: (r.pop("score", 0), r["_mtime"]), reverse=True)
        for item in resultados:
            item.pop("_mtime", None)

        return {
            "ready": listo,
            "query": consulta,
            "count": min(len(resultados), limite),
            "total_matches": len(resultados),
            "truncated_index": truncado,
            "error": error,
            "results": resultados[:limite],
        }

    def recientes(self, *, limite: int = 10, bajo: str = "") -> dict:
        listo = self.asegurar(5.0)
        with self._lock:
            entradas = self._entradas
            truncado = self._truncado
            error = self._error

        prefijo = normalizar(bajo).strip("/")
        filtradas = [
            e
            for e in entradas
            if not prefijo or normalizar(e.ruta).startswith(prefijo)
        ]
        # `entradas` ya viene ordenado por fecha descendente.
        return {
            "ready": listo,
            "under": bajo,
            "files": [
                {
                    "path": e.ruta,
                    "name": e.nombre,
                    "size": e.tamano,
                    "modified": e.modificado,
                }
                for e in filtradas[:limite]
            ],
            "truncated_index": truncado,
            "error": error,
        }

    def estado(self) -> dict:
        listo = self.asegurar(0.0)
        with self._lock:
            total = len(self._entradas)
            bytes_totales = sum(e.tamano for e in self._entradas)
            return {
                "ready": listo and total > 0,
                "root": str(self.raiz),
                "files": total,
                "bytes": bytes_totales,
                "built_at": (
                    datetime.fromtimestamp(
                        self._construido_en, tz=timezone.utc
                    ).isoformat()
                    if self._construido_en
                    else None
                ),
                "stale": bool(self._entradas)
                and (time.time() - self._construido_en) >= self.refresh_s,
                "building": self._construyendo,
                "truncated": self._truncado,
                "error": self._error,
                "refresh_s": self.refresh_s,
                "max_files": self.max_files,
            }


def _recorte(texto: str, tokens: list[str], ancho: int = 90) -> str:
    """Fragmento alrededor de la primera coincidencia, en una sola línea."""
    plano = texto.lower()
    posicion = -1
    for token in tokens:
        posicion = plano.find(token)
        if posicion >= 0:
            break
    if posicion < 0:
        posicion = 0
    inicio = max(0, posicion - ancho)
    fin = min(len(texto), posicion + ancho)
    fragmento = " ".join(texto[inicio:fin].split())
    if inicio > 0:
        fragmento = "…" + fragmento
    if fin < len(texto):
        fragmento = fragmento + "…"
    return fragmento
