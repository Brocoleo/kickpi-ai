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

import json
import os
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Versión del formato en disco. Si cambia, el caché viejo se ignora.
_CACHE_VERSION = 1

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
        cache_path: str | Path | None = None,
    ) -> None:
        self.raiz = Path(raiz)
        self.max_files = max_files
        self.refresh_s = refresh_s
        self.cache_path = Path(cache_path) if cache_path else None
        self._lock = threading.RLock()
        self._entradas: list[Entrada] = []
        self._construido_en = 0.0
        self._construyendo = False
        self._truncado = False
        self._error: str | None = None
        self._cache_visto = False
        self._cache_leido = False

    # ------------------------------------------------------------------ #
    # Caché en disco
    # ------------------------------------------------------------------ #
    def _leer_cache(self) -> None:
        """Carga el índice guardado, si existe y corresponde a esta raíz.

        Se lee una sola vez. Si el archivo está corrupto o es de otro
        formato, se ignora sin ruido: el siguiente recorrido lo reescribe.
        """
        self._cache_visto = True
        if not self.cache_path:
            return
        try:
            datos = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if datos.get("version") != _CACHE_VERSION:
                return
            if Path(datos.get("root", "")) != self.raiz:
                return
            entradas = [
                Entrada(
                    ruta=str(item[0]),
                    nombre=str(item[1]),
                    tamano=int(item[2]),
                    mtime=float(item[3]),
                )
                for item in datos.get("files", [])
            ]
        except (OSError, ValueError, TypeError, IndexError):
            return

        with self._lock:
            self._entradas = entradas
            self._construido_en = float(datos.get("built_at") or 0.0)
            self._truncado = bool(datos.get("truncated"))
            self._error = None
            self._cache_leido = True

    def _guardar_cache(self) -> None:
        if not self.cache_path:
            return
        with self._lock:
            entradas = self._entradas
            construido = self._construido_en
            truncado = self._truncado
        payload = {
            "version": _CACHE_VERSION,
            "root": str(self.raiz),
            "built_at": construido,
            "truncated": truncado,
            "files": [[e.ruta, e.nombre, e.tamano, e.mtime] for e in entradas],
        }
        temporal = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporal.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporal, self.cache_path)
        except OSError:
            # Un caché que no se puede escribir no debe romper las búsquedas.
            temporal.unlink(missing_ok=True)

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
        if entradas and error is None:
            self._guardar_cache()

    def asegurar(self, espera_s: float = 5.0) -> bool:
        """Garantiza un índice (aunque sea viejo) y lanza refresco si toca.

        Devuelve True si hay algo que consultar. Nunca bloquea más de
        `espera_s`: si el primer recorrido tarda (disco grande), la consulta
        responde «indexando» en vez de colgar el turno del modelo.
        """
        if not self._cache_visto:
            self._leer_cache()
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

    @staticmethod
    def _normalizar_ruta(ruta: str) -> str:
        """Ruta relativa limpia; rechaza cualquier intento de escape."""
        limpia = (ruta or "").strip().strip("/")
        if not limpia:
            return ""
        partes = [p for p in limpia.split("/") if p and p != "."]
        if any(p == ".." for p in partes):
            raise ValueError("ruta no válida")
        return "/".join(partes)

    def _dentro_de_raiz(self, destino: Path) -> bool:
        raiz = self.raiz.resolve()
        resuelto = destino.resolve()
        return resuelto == raiz or raiz in resuelto.parents

    def listar(self, ruta: str = "", *, limite: int = 300) -> dict:
        """Lista UNA carpeta del NAS (carpetas primero, luego archivos).

        Se lee del sistema de archivos y no del índice: así aparecen también
        carpetas vacías y archivos recién copiados. Es una sola llamada a
        `scandir`, no un recorrido completo.
        """
        try:
            rel = self._normalizar_ruta(ruta)
        except ValueError:
            return {
                "ready": False,
                "path": ruta,
                "error": "ruta no válida",
                "dirs": [],
                "files": [],
            }

        base = (self.raiz / rel) if rel else self.raiz
        if not self._dentro_de_raiz(base):
            return {
                "ready": False,
                "path": rel,
                "error": "ruta fuera de la NAS",
                "dirs": [],
                "files": [],
            }

        try:
            iterator = os.scandir(base)
        except OSError as exc:
            return {
                "ready": False,
                "path": rel,
                "error": f"no se pudo abrir la carpeta: {exc}",
                "dirs": [],
                "files": [],
            }

        carpetas: list[dict] = []
        archivos: list[dict] = []
        with iterator:
            for item in iterator:
                if len(carpetas) + len(archivos) >= limite:
                    break
                nombre = item.name
                if nombre.startswith(".") or nombre.lower() in _SKIP_DIRS:
                    continue
                try:
                    if item.is_dir(follow_symlinks=False):
                        carpetas.append(
                            {
                                "name": nombre,
                                "path": f"{rel}/{nombre}" if rel else nombre,
                            }
                        )
                    elif item.is_file(follow_symlinks=False):
                        stat = item.stat(follow_symlinks=False)
                        archivos.append(
                            {
                                "name": nombre,
                                "path": f"{rel}/{nombre}" if rel else nombre,
                                "size": stat.st_size,
                                "modified": datetime.fromtimestamp(
                                    stat.st_mtime, tz=timezone.utc
                                ).isoformat(),
                                "ext": Path(nombre).suffix.lower(),
                            }
                        )
                except OSError:
                    continue

        carpetas.sort(key=lambda d: d["name"].casefold())
        archivos.sort(key=lambda f: f["modified"], reverse=True)
        with self._lock:
            truncado = self._truncado
        return {
            "ready": True,
            "path": rel,
            "parent": rel.rsplit("/", 1)[0] if "/" in rel else "",
            "dirs": carpetas,
            "files": archivos,
            "truncated_index": truncado,
            "error": None,
        }

    def archivo(self, ruta: str) -> Path:
        """Devuelve la ruta real de un archivo dentro de la NAS.

        Lanza ValueError si la ruta escapa (incluidos symlinks que apunten
        fuera) y FileNotFoundError si no es un archivo.
        """
        rel = self._normalizar_ruta(ruta)
        if not rel:
            raise ValueError("ruta vacía")
        destino = self.raiz / rel
        if not self._dentro_de_raiz(destino):
            raise ValueError("ruta fuera de la NAS")
        resuelto = destino.resolve()
        if not resuelto.is_file():
            raise FileNotFoundError(rel)
        return resuelto

    def refresh(self, subruta: str = "") -> dict:
        """Invalida una parte del índice (o todo) y fuerza re-escaneo.

        Si `subruta` está vacía, invalida el índice completo.
        Si es un path relativo (ej. "files/Entrada"), solo marca como
        obsoleto lo que cuelga de ahí y vuelve a recorrer esa rama.
        """
        try:
            rel = self._normalizar_ruta(subruta)
        except ValueError:
            return {"ok": False, "error": "subruta no válida"}

        base = (self.raiz / rel) if rel else self.raiz
        if not self._dentro_de_raiz(base):
            return {"ok": False, "error": "subruta fuera de la NAS"}

        with self._lock:
            # Si es invalidación total, vaciar y lanzar reconstrucción completa.
            if not rel:
                self._entradas = []
                self._construido_en = 0.0
                self._truncado = False
                self._error = None
                self._cache_leido = False
                if not self._construyendo:
                    self._construyendo = True
                    threading.Thread(
                        target=self._construir, name="nas-index-full", daemon=True
                    ).start()
                return {"ok": True, "scope": "full", "message": "reindexación completa iniciada"}

            # Invalidación parcial: filtrar entradas que NO están bajo la subruta
            prefijo = rel.rstrip("/") + "/"
            antes = len(self._entradas)
            self._entradas = [
                e for e in self._entradas if not normalizar(e.ruta).startswith(normalizar(prefijo))
            ]
            eliminadas = antes - len(self._entradas)

        # Recorrer solo la subruta y fusionar
        entradas_nuevas, truncado, error = self._recorrer_subruta(base, rel)
        with self._lock:
            self._entradas.extend(entradas_nuevas)
            self._entradas.sort(key=lambda e: e.mtime, reverse=True)
            self._truncado = truncado
            self._error = error
            self._construido_en = time.time()
            if error is None and (entradas_nuevas or eliminadas > 0):
                self._guardar_cache()

        return {
            "ok": True,
            "scope": "partial",
            "path": rel,
            "removed": eliminadas,
            "added": len(entradas_nuevas),
            "error": error,
        }

    def _recorrer_subruta(self, base: Path, rel: str) -> tuple[list[Entrada], bool, str | None]:
        """Recorre solo una subruta (para refresh parcial)."""
        entradas: list[Entrada] = []
        truncado = False
        error: str | None = None

        if not base.is_dir():
            return entradas, False, f"la subruta no existe o no es un directorio: {base}"

        pendientes = [base]
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
                            rel_item = os.path.relpath(item.path, self.raiz).replace(os.sep, "/")
                            entradas.append(
                                Entrada(
                                    ruta=rel_item,
                                    nombre=item.name,
                                    tamano=stat.st_size,
                                    mtime=stat.st_mtime,
                                )
                            )
                    except OSError:
                        continue

        truncado = len(entradas) >= self.max_files
        entradas.sort(key=lambda e: e.mtime, reverse=True)
        return entradas, truncado, error

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
                "cache": str(self.cache_path) if self.cache_path else None,
                "cache_loaded": self._cache_leido,
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
