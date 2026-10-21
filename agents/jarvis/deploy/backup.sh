#!/usr/bin/env bash
# Copias de seguridad del stack Jarvis: saca, trae, verifica y replica.
#
#   ./deploy/backup.sh              copia, la trae al Mac y la replica al Xeon
#   ./deploy/backup.sh --drill      verifica la última copia (restauración)
#   ./deploy/backup.sh --drill F    verifica una copia concreta
#   ./deploy/backup.sh --list       qué copias hay y cuáles están verificadas
#   ./deploy/backup.sh --restore    cómo restaurar de verdad (instrucciones)
#
# No necesita sudo: lee las bases desde la placa (son legibles) y nunca toca
# los contenedores.
#
# Tres copias, tres máquinas:
#   1. la placa      /mnt/data/respaldos/          (origen del stack)
#   2. el MacBook    ~/respaldos/jarvis/           (esta máquina, y viaja)
#   3. el Xeon       ~/respaldos/jarvis/           (cuando está encendido)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOARD="${BOARD:-leandro@100.111.200.54}"
KEY="${KEY:-$HOME/.ssh/id_ed25519_jarvis}"
DEST="${DEST:-$HOME/respaldos/jarvis}"
XEON="${XEON:-leandro@100.73.247.126}"
KEEP_LOCAL="${KEEP_LOCAL:-30}"

SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$BOARD")

verde() { printf '\033[32m%s\033[0m\n' "$*"; }
rojo() { printf '\033[31m%s\033[0m\n' "$*"; }
aviso() { printf '\033[33m%s\033[0m\n' "$*"; }

sha256_de() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

# Verificación de una copia. La hace Python (stdlib) porque tiene que mirar
# dentro de las bases: integridad, tablas y número de filas del manifiesto.
drill() {
    python3 - "$1" <<'PY'
import hashlib
import json
import pathlib
import sqlite3
import sys
import tarfile
import tempfile

archivo = pathlib.Path(sys.argv[1])
fallos: list[str] = []

if not archivo.is_file():
    print(f"no existe: {archivo}")
    sys.exit(2)

tmp = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-drill-"))
try:
    with tarfile.open(archivo, "r:gz") as tar:
        for miembro in tar.getmembers():
            destino = (tmp / miembro.name).resolve()
            if tmp.resolve() not in destino.parents and destino != tmp.resolve():
                fallos.append(f"ruta insegura en el paquete: {miembro.name}")
                continue
            tar.extract(miembro, tmp, set_attrs=False)
except (tarfile.TarError, EOFError, OSError) as exc:
    print(f"FALLO: el paquete está dañado ({exc})")
    sys.exit(1)

manifiesto = tmp / "MANIFEST.tsv"
if not manifiesto.is_file():
    print(f"FALLO: el paquete no trae MANIFEST.tsv ({archivo})")
    sys.exit(1)

meta: dict[str, str] = {}
esperado: dict[str, dict] = {}
for linea in manifiesto.read_text(encoding="utf-8").splitlines():
    if not linea or linea.startswith("#"):
        continue
    partes = linea.split("\t")
    if len(partes) == 2:
        meta[partes[0]] = partes[1]
    elif len(partes) == 5:
        ruta, tamano, sha, tipo, detalle = partes
        esperado[ruta] = {
            "bytes": int(tamano),
            "sha256": sha,
            "tipo": tipo,
            "detalle": detalle,
        }

print(f"paquete   : {archivo.name} ({archivo.stat().st_size / 1024:.0f} KB)")
print(f"creado    : {meta.get('created_at', '?')}")
print(f"host      : {meta.get('host', '?')}")
print(f"archivos  : {len(esperado)}")
print()

print(f"{'archivo':<40} {'bytes':>9}  {'filas':<28} estado")
print("-" * 96)

for ruta, info in sorted(esperado.items()):
    destino = tmp / ruta
    if not destino.is_file():
        fallos.append(f"falta {ruta}")
        print(f"{ruta:<40} {'-':>9}  {'-':<28} FALTA")
        continue

    datos = destino.read_bytes()
    sha = hashlib.sha256(datos).hexdigest()
    filas = "-"
    estado = "ok"

    if sha != info["sha256"]:
        fallos.append(f"{ruta}: sha256 no coincide")
        estado = "SHA MAL"

    if len(datos) != info["bytes"]:
        fallos.append(f"{ruta}: tamaño {len(datos)} != {info['bytes']}")
        estado = "TAMAÑO MAL"

    # Solo las bases SQLite se abren: FileBrowser usa bbolt y abrirlo como
    # SQLite daría un falso positivo de corrupción.
    if info.get("tipo") == "sqlite":
        try:
            con = sqlite3.connect(f"file:{destino}?mode=ro", uri=True)
            integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
            conteos = json.loads(info["detalle"] or "{}")
            reales = {}
            for tabla in conteos:
                reales[tabla] = con.execute(
                    f'SELECT COUNT(*) FROM "{tabla}"'
                ).fetchone()[0]
            con.close()

            if integridad != "ok":
                fallos.append(f"{ruta}: integrity_check={integridad}")
                estado = "CORRUPTA"
            if fk:
                fallos.append(f"{ruta}: {len(fk)} violaciones de clave foránea")
                estado = "FK MAL"
            if reales != conteos:
                fallos.append(f"{ruta}: filas distintas del manifiesto")
                estado = "FILAS MAL"
            filas = ",".join(f"{t}={n}" for t, n in sorted(reales.items())) or "-"
        except Exception as exc:  # noqa: BLE001
            fallos.append(f"{ruta}: no se pudo abrir ({exc})")
            estado = "ILEGIBLE"
            filas = "-"

    if ruta.endswith((".env", "docker-compose.yml")) and len(datos) < 20:
        fallos.append(f"{ruta}: contenido sospechosamente corto")
        estado = "VACÍO"

    print(f"{ruta:<40} {len(datos):>9}  {filas[:28]:<28} {estado}")

print()
if fallos:
    print(f"FALLO: {len(fallos)} problema(s)")
    for fallo in fallos:
        print(f"  - {fallo}")
    sys.exit(1)

print("OK: la copia se puede restaurar (integridad, hashes y filas coinciden)")
PY
}

restaurar_ayuda() {
    cat <<'TXT'
Restauración real (requiere docker y sudo, hazla tú):

  1. Elige una copia verificada:      ./deploy/backup.sh --list
  2. Compruébala:                     ./deploy/backup.sh --drill <archivo>
  3. En la placa, con el stack parado:

       mkdir -p /tmp/restauracion && tar -C /tmp/restauracion -xzf <archivo>
       docker compose -f ~/jarvis/docker-compose.yml down
       sudo cp /tmp/restauracion/data/agent/jarvis.db      /mnt/data/docker/jarvis_data/jarvis.db
       sudo cp /tmp/restauracion/data/executor/audit.db    /mnt/data/docker/executor_data/audit.db
       sudo cp /tmp/restauracion/data/filebrowser/*        /mnt/data/docker/filebrowser/data/
       cp /tmp/restauracion/stack/docker-compose.yml ~/jarvis/
       cp /tmp/restauracion/stack/.env ~/jarvis/
       docker compose -f ~/jarvis/docker-compose.yml up -d
       ./deploy-all.sh --check

  Las bases viejas son compatibles: el agente migra el esquema al arrancar
  (columnas nuevas, nunca destructivo).
TXT
}

case "${1:-}" in
    --drill)
        ARCHIVO="${2:-}"
        if [ -z "$ARCHIVO" ]; then
            ARCHIVO="$(ls -1t "$DEST"/jarvis-*.tgz 2>/dev/null | head -1 || true)"
        fi
        if [ -z "$ARCHIVO" ]; then
            rojo "no hay copias en $DEST"
            exit 1
        fi
        drill "$ARCHIVO"
        ;;

    --list)
        if [ ! -d "$DEST" ]; then
            aviso "todavía no hay copias locales en $DEST"
            exit 1
        fi
        printf '%-34s %10s  %s\n' "COPIA" "TAMAÑO" "SHA256"
        for f in $(ls -1t "$DEST"/jarvis-*.tgz 2>/dev/null); do
            printf '%-34s %10s  %s\n' \
                "$(basename "$f")" "$(du -h "$f" | cut -f1)" "$(sha256_de "$f")"
        done
        ;;

    --restore)
        restaurar_ayuda
        ;;

    "")
        mkdir -p "$DEST"
        chmod 700 "$DEST"

        echo "== 1/4 copiando en la placa (sin sudo)"
        SALIDA="$("${SSH[@]}" 'bash -s' < "$HERE/remote/backup.sh")"
        LECTURA="$(echo "$SALIDA" | grep '^TARBALL ' | tail -1)"
        if [ -z "$LECTURA" ]; then
            rojo "la placa no devolvió ninguna copia"
            echo "$SALIDA"
            exit 1
        fi
        REMOTO="$(echo "$LECTURA" | cut -d' ' -f2)"
        SHA_REMOTO="$(echo "$LECTURA" | cut -d' ' -f3)"
        BYTES="$(echo "$LECTURA" | cut -d' ' -f4)"
        verde "  $(basename "$REMOTO") ($((BYTES / 1024)) KB)"

        echo "== 2/4 trayéndola al Mac"
        scp -q -i "$KEY" -o BatchMode=yes "$BOARD:$REMOTO" "$DEST/"
        LOCAL="$DEST/$(basename "$REMOTO")"
        chmod 600 "$LOCAL"
        SHA_LOCAL="$(sha256_de "$LOCAL")"
        if [ "$SHA_LOCAL" != "$SHA_REMOTO" ]; then
            rojo "  la copia llegó corrupta (sha256 distinto)"
            exit 1
        fi
        verde "  $LOCAL (sha256 idéntico)"

        echo "== 3/4 verificando la restauración"
        if drill "$LOCAL"; then
            :
        else
            # Una copia que no se puede restaurar no es una copia: se queda
            # en la placa para diagnóstico, pero no ensucia el Mac ni se
            # propaga como si fuera buena.
            rm -f "$LOCAL"
            rojo "  la copia NO es restaurable: descartada del Mac (sigue en la placa)"
            exit 1
        fi

        echo "== 4/4 tercera copia (Xeon)"
        if ssh -o BatchMode=yes -o ConnectTimeout=6 "$XEON" true 2>/dev/null; then
            ssh -o BatchMode=yes "$XEON" 'mkdir -p ~/respaldos/jarvis && chmod 700 ~/respaldos/jarvis'
            rsync -a --partial "$LOCAL" "$XEON:~/respaldos/jarvis/"
            ssh -o BatchMode=yes "$XEON" \
                "ls -1t ~/respaldos/jarvis/jarvis-*.tgz | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm -f"
            verde "  copiada al Xeon"
        else
            aviso "  Xeon no alcanzable: cuando lo enciendas, ./deploy/backup.sh"
        fi

        # Rotación local: la placa y el Xeon rotan aparte.
        ls -1t "$DEST"/jarvis-*.tgz | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm -f

        echo
        verde "copia hecha y verificada: $(basename "$LOCAL")"
        echo "  placa : $REMOTO"
        echo "  Mac   : $LOCAL"
        ;;

    *)
        echo "uso: $0 [--drill [archivo] | --list | --restore]" >&2
        exit 2
        ;;
esac
