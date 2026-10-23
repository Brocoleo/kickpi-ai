#!/usr/bin/env bash
# =============================================================================
# check-updates.sh — avisa de versiones nuevas. NO instala nada.
#
#   ./scripts/check-updates.sh
#
# Por qué solo avisa: el stack está fijado a versiones probadas. Un `pull`
# automático puede romper FileBrowser (v1 → v2 cambia la base de datos) o el
# agente en mitad de la noche. Este script compara lo fijado en el repo con lo
# último publicado y deja la decisión en manos del humano.
#
# Salida: una línea por componente (OK / NUEVA) y el enlace a las notas.
# Código de salida: siempre 0 (es informativo; no bloquea un deploy).
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="$HERE/docker-compose.yml"

verde() { printf '\033[32m%s\033[0m\n' "$*"; }
ambar() { printf '\033[33m%s\033[0m\n' "$*"; }
rojo() { printf '\033[31m%s\033[0m\n' "$*"; }

if [ ! -f "$COMPOSE" ]; then
  rojo "No encuentro docker-compose.yml en $HERE"
  exit 0
fi

# --- FileBrowser Quantum ----------------------------------------------------
actual="$(
  grep -oE 'image:[[:space:]]*gtstef/filebrowser:[^[:space:]]+' "$COMPOSE" \
    | head -1 | sed 's/.*://' | sed -E 's/^v//; s/-slim$//; s/-stable$//'
)"
if [ -z "$actual" ]; then
  ambar "· FileBrowser: no está fijado en docker-compose.yml (¿otro nombre de imagen?)"
else
  ultimas="$(
    curl -fsSL --max-time 12 \
      "https://api.github.com/repos/gtsteffaniak/filebrowser/tags?per_page=100" \
      | python3 -c '
import json, re, sys
try:
    tags = json.load(sys.stdin)
except Exception:
    sys.exit(0)
def clave(n):
    m = re.match(r"v(\d+)\.(\d+)\.(\d+)-stable$", n)
    return tuple(map(int, m.groups())) if m else None
estables = [t["name"] for t in tags if clave(t["name"])]
if estables:
    print(max(estables, key=clave).removeprefix("v").removesuffix("-stable"))
' || true
  )"
  if [ -z "$ultimas" ]; then
    ambar "· FileBrowser: no se pudo consultar GitHub (sin red o límite de API)"
  elif [ "$ultimas" = "$actual" ]; then
    verde "✔ FileBrowser: $actual (última)"
  else
    ambar "↑ FileBrowser: fijado $actual · disponible $ultimas"
    echo "    Antes de subir de v1 a v2: lee la migración (cambia la BD)."
    echo "    https://github.com/gtsteffaniak/filebrowser/releases"
  fi
fi

# --- Imágenes del stack (solo inventario) -----------------------------------
echo
echo "Imágenes fijadas en docker-compose.yml:"
grep -nE '^[[:space:]]*image:' "$COMPOSE" \
  | sed -E 's/^([0-9]+):[[:space:]]*image:[[:space:]]*/\1  /' \
  | while IFS= read -r linea; do
      printf '  %s\n' "$linea"
    done

echo
echo "Para actualizar una imagen: cambia la etiqueta, ejecuta"
echo "  ./deploy-all.sh --full   (o --filebrowser / --tools según toque)"
echo "y revisa el resultado en /health. Nunca en mitad de una copia."
