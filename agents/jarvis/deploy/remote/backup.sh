#!/usr/bin/env bash
# Copia consistente del stack Jarvis. Se ejecuta EN LA PLACA, sin sudo.
#
# Por qué no un simple `cp`: las bases están en modo WAL y el agente escribe
# mientras se copian. Copiar el .db sin su WAL puede producir un archivo
# truncado o incoherente. Aquí se usa la API de backup de SQLite, que toma una
# instantánea estable aunque haya escrituras en curso.
#
# El paquete incluye datos + configuración (compose, .env, catálogos) y un
# MANIFEST con tamaño, sha256 y número de filas por tabla. El manifiesto es lo
# que permite que el simulacro de restauración compruebe algo de verdad.
#
# Salida (una línea, fácil de parsear desde el Mac):
#   TARBALL <ruta> <sha256> <bytes>
set -euo pipefail

STACK_DIR="${STACK_DIR:-$HOME/jarvis}"
DATA_DIR="${DATA_DIR:-/mnt/data/docker}"
OUT_DIR="${OUT_DIR:-/mnt/data/respaldos}"
KEEP="${KEEP:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
TARBALL="$OUT_DIR/jarvis-$STAMP.tgz"

mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-backup.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

python3 - "$WORK" "$STACK_DIR" "$DATA_DIR" <<'PY'
"""Copia cada base con la API de backup y escribe el manifiesto."""
import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

work = pathlib.Path(sys.argv[1])
stack = pathlib.Path(sys.argv[2])
data = pathlib.Path(sys.argv[3])

# (origen, ruta dentro del paquete, ¿es SQLite?)
ITEMS = [
    (data / "jarvis_data" / "jarvis.db", "data/agent/jarvis.db", True),
    (data / "executor_data" / "audit.db", "data/executor/audit.db", True),
    # FileBrowser usa bbolt, no SQLite: se copia tal cual. Es solo su índice
    # de usuarios/compartidos; si la copia saliera incoherente, FileBrowser lo
    # reconstruye reexplorando el disco. El config.yaml sí es imprescindible.
    (data / "filebrowser" / "data" / "database.db", "data/filebrowser/database.db", False),
    (data / "filebrowser" / "data" / "config.yaml", "data/filebrowser/config.yaml", False),
    (stack / "docker-compose.yml", "stack/docker-compose.yml", False),
    (stack / ".env", "stack/.env", False),
    (stack / "executor" / "config" / "hosts.yml", "stack/executor/config/hosts.yml", False),
    (stack / "executor" / "config" / "catalog.yml", "stack/executor/config/catalog.yml", False),
]

filas = []
faltantes = []

for origen, destino_rel, es_sqlite in ITEMS:
    if not origen.exists():
        faltantes.append(str(origen))
        continue

    destino = work / destino_rel
    destino.parent.mkdir(parents=True, exist_ok=True)

    detalle = ""
    if es_sqlite:
        # Copiar el .db abriéndolo en solo lectura NO funciona aquí: el
        # directorio de datos es de root y SQLite no puede crear el -shm.
        # Se copia el trío (db, -wal, -shm) y se trabaja sobre la copia; el
        # checkpoint deja el WAL aplicado y el backup sale completo.
        with tempfile.TemporaryDirectory(dir=work, prefix="snap-") as tmpd:
            copia = pathlib.Path(tmpd) / "copia.db"
            shutil.copy2(origen, copia)
            for sufijo in ("-wal", "-shm"):
                extra = pathlib.Path(str(origen) + sufijo)
                if extra.exists():
                    shutil.copy2(extra, str(copia) + sufijo)

            src = sqlite3.connect(copia)
            try:
                src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                dst = sqlite3.connect(destino)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()

        # Conteo por tabla: sirve para detectar una copia vacía o a medias.
        con = sqlite3.connect(f"file:{destino}?mode=ro", uri=True)
        try:
            tablas = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            conteos = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tablas}
        finally:
            con.close()
        detalle = json.dumps(conteos, separators=(",", ":"), sort_keys=True)
    else:
        shutil.copy2(origen, destino)
        if origen.name == ".env":
            os.chmod(destino, 0o600)

    sha = hashlib.sha256(destino.read_bytes()).hexdigest()
    filas.append(
        (
            destino_rel,
            destino.stat().st_size,
            sha,
            "sqlite" if es_sqlite else "archivo",
            detalle,
        )
    )

manifiesto = work / "MANIFEST.tsv"
with manifiesto.open("w", encoding="utf-8") as fh:
    fh.write("# jarvis-backup 1\n")
    fh.write(f"created_at\t{datetime.now(timezone.utc).isoformat()}\n")
    fh.write(f"host\t{os.uname().nodename}\n")
    fh.write("# ruta\ttamano\tsha256\ttipo\tdetalle\n")
    for ruta, tamano, sha, tipo, detalle in sorted(filas):
        fh.write(f"{ruta}\t{tamano}\t{sha}\t{tipo}\t{detalle}\n")
    if faltantes:
        fh.write("# AVISO: no existian al copiar:\n")
        for ruta in faltantes:
            fh.write(f"#   {ruta}\n")

(work / "README.txt").write_text(
    "Copia del stack Jarvis.\n\n"
    "Contenido:\n"
    "  data/    bases de datos (instantanea consistente de SQLite)\n"
    "  stack/   compose, .env y catalogos necesarios para levantar de nuevo\n"
    "  MANIFEST.tsv  tamano, sha256 y filas por tabla de cada base\n\n"
    "El .env contiene secretos: este paquete debe quedar en un medio privado\n"
    "(permisos 600) y no debe subirse a un repositorio.\n",
    encoding="utf-8",
)

if faltantes:
    print("AVISO: elementos ausentes:", ", ".join(faltantes), file=sys.stderr)
PY

tar -C "$WORK" -czf "$TARBALL" .
chmod 600 "$TARBALL"

# Rotación: conserva las KEEP más recientes.
mapfile -t VIEJOS < <(ls -1t "$OUT_DIR"/jarvis-*.tgz 2>/dev/null | tail -n +$((KEEP + 1)))
if [ "${#VIEJOS[@]}" -gt 0 ]; then
    rm -f -- "${VIEJOS[@]}"
fi

SHA="$(sha256sum "$TARBALL" | cut -d' ' -f1)"
echo "TARBALL $TARBALL $SHA $(stat -c %s "$TARBALL")"
