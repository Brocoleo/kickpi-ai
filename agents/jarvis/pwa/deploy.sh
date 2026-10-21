#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Compila la PWA, la publica en la placa y VERIFICA que se sirva.
#
# Problema que resuelve: la PWA se compilaba a mano y quedaba un build viejo
# horneado en la imagen / sin subir. Ahora hay un solo comando, con
# verificación de extremo a extremo: build local == archivo remoto == /health.
#
# Uso:
#   ./deploy.sh              compila, sincroniza y verifica
#   ./deploy.sh --check      solo verifica (no compila ni sincroniza)
#   ./deploy.sh --watch      espera cambios en src/ y reintenta el deploy
#   ./deploy.sh --test       corre las pruebas de la PWA antes del deploy
#
# Requisitos: npm, rsync, ssh con la clave de la placa, python3 en la placa.
# =============================================================================
set -euo pipefail

BOARD="${JARVIS_BOARD:-leandro@100.111.200.54}"
SSH_KEY="${JARVIS_SSH_KEY:-$HOME/.ssh/id_ed25519_jarvis}"
REMOTE_STATIC="${JARVIS_REMOTE_STATIC:-/home/leandro/jarvis/agent/static}"
HEALTH_URL="${JARVIS_HEALTH_URL:-http://127.0.0.1:8888/health}"
ASSET_RE='index-[A-Za-z0-9_-]+\.js'

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# El build de Vite escribe en ../agent/static (ver vite.config.ts).
STATIC="${JARVIS_LOCAL_STATIC:-$HERE/../agent/static}"
SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=8)
RSYNC_SSH="ssh -i $SSH_KEY -o BatchMode=yes"

rojo() { printf '\033[31m%s\033[0m\n' "$*"; }
verde() { printf '\033[32m%s\033[0m\n' "$*"; }
ambar() { printf '\033[33m%s\033[0m\n' "$*"; }

remote() { ssh "${SSH_OPTS[@]}" "$BOARD" "$@"; }

asset_local() { grep -oE "$ASSET_RE" "$STATIC/index.html" 2>/dev/null | head -1 || true; }

asset_remoto() {
  remote "grep -oE '$ASSET_RE' '$REMOTE_STATIC/index.html' 2>/dev/null | head -1" || true
}

asset_health() {
  remote "curl -sS -f --max-time 5 '$HEALTH_URL'" \
    | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("static_asset") or "")
except Exception: print("")' 2>/dev/null || true
}

verificar() {
  local local_a remoto_a health_a errores=0
  local_a="$(asset_local)"
  remoto_a="$(asset_remoto)"
  health_a="$(asset_health)"

  echo "  build local   : ${local_a:-<ninguno>}"
  echo "  placa (disco) : ${remoto_a:-<ninguno>}"
  echo "  /health sirve : ${health_a:-<vacío o inaccesible>}"

  if [ -z "$local_a" ]; then
    rojo "  ✗ no hay build local (falta agent/static/index.html)"
    return 1
  fi
  if [ "$local_a" != "$remoto_a" ]; then
    rojo "  ✗ la placa tiene un build DISTINTO ($remoto_a)"
    errores=1
  fi
  # Decir "desplegado" con Jarvis inaccesible es peor que fallar: /health
  # debe responder y debe reportar EXACTAMENTE el bundle publicado.
  if [ -z "$health_a" ]; then
    rojo "  ✗ /health no responde o no reporta static_asset: Jarvis no está sirviendo la PWA"
    rojo "    (si el backend es anterior a este cambio, recompila y recrea jarvis-agent)"
    errores=1
  elif [ "$health_a" != "$local_a" ]; then
    rojo "  ✗ /health sirve otro bundle ($health_a)"
    errores=1
  fi
  return $errores
}

sincronizar() {
  # Guarda anti-desastre: jamás sincronizar sin un build real.
  if [ ! -s "$STATIC/index.html" ] || [ -z "$(asset_local)" ]; then
    rojo "✗ abortado: no hay build válido en $STATIC (no se toca la placa)"
    return 1
  fi

  # Publicación en dos fases para no romper clientes abiertos:
  #   1. primero los assets nuevos (los bundles viejos se CONSERVAN);
  #   2. al final el shell (index.html, sw.js, manifest), que es lo que
  #      apunta a los assets nuevos.
  # Un iPhone con el HTML viejo sigue encontrando su bundle antiguo.
  rsync -az -e "$RSYNC_SSH" \
    --include='/assets/***' --exclude='*' \
    "$STATIC/" "$BOARD:$REMOTE_STATIC/"
  rsync -az -e "$RSYNC_SSH" \
    --exclude='/assets/' \
    "$STATIC/" "$BOARD:$REMOTE_STATIC/"

  # Limpieza diferida: se borran bundles con más de KEEP_DAYS días. Suficiente
  # para que una PWA abierta termine de actualizarse, sin acumular basura.
  local keep_days="${JARVIS_KEEP_DAYS:-7}"
  remote "find '$REMOTE_STATIC/assets' -type f -mtime +$keep_days -delete" || true
}

deploy() {
  echo "== 1/3 compilando PWA"
  (cd "$HERE" && npm run build)
  echo "== 2/3 sincronizando a $BOARD"
  sincronizar
  echo "== 3/3 verificando"
  if verificar; then
    verde "✔ desplegado y verificado: $(asset_local)"
    return 0
  fi
  rojo "✗ verificación fallida"
  return 1
}

case "${1:-}" in
  --check)
    echo "== verificando (sin compilar)"
    verificar && verde "✔ todo coincide: $(asset_local)"
    ;;
  --test)
    (cd "$HERE" && npm run test)
    deploy
    ;;
  --watch)
    echo "== modo vigilancia (Ctrl-C para salir)"
    deploy || true
    if command -v inotifywait >/dev/null 2>&1; then
      while inotifywait -q -r -e modify,create,delete,move \
        "$HERE/src" "$HERE/index.html" "$HERE/public" >/dev/null 2>&1; do
        echo
        echo "== cambio detectado $(date '+%H:%M:%S')"
        sleep 1
        deploy || true
      done
    else
      ambar "inotifywait no está instalado: uso sondeo cada 5 s"
      ambar "(instálalo con: sudo apt install inotify-tools)"
      PREV_HASH="$(find "$HERE/src" "$HERE/index.html" "$HERE/public" -type f -printf '%T@ %p\n' 2>/dev/null | sort | md5sum)"
      while true; do
        sleep 5
        NOW_HASH="$(find "$HERE/src" "$HERE/index.html" "$HERE/public" -type f -printf '%T@ %p\n' 2>/dev/null | sort | md5sum)"
        if [ "$NOW_HASH" != "$PREV_HASH" ]; then
          PREV_HASH="$NOW_HASH"
          echo
          echo "== cambio detectado $(date '+%H:%M:%S')"
          deploy || true
        fi
      done
    fi
    ;;
  "")
    deploy
    ;;
  *)
    rojo "opción desconocida: $1"
    echo "uso: $0 [--check|--watch|--test]"
    exit 2
    ;;
esac
