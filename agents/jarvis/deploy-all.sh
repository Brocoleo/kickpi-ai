#!/usr/bin/env bash
# =============================================================================
# deploy-all.sh — Construir, publicar y VERIFICAR todo (agente + ejecutor + PWA)
#                 con un solo comando.
#
# El detalle que hace que esto funcione: sudo y TTY.
#   Cada `ssh` abre una sesión nueva y el "ticket" de sudo NO se comparte
#   entre sesiones. Por eso todo lo que necesita sudo viaja en UN SOLO `ssh`
#   con TTY (-t): la contraseña se pide una vez y vale para todo el bloque.
#   Y por eso el comando remoto va como ARGUMENTO de ssh, nunca por stdin
#   (heredoc): si se ocupa stdin, ssh no puede darte la terminal para
#   escribir la contraseña.
#
# Uso:
#   ./deploy-all.sh --full        agente + ejecutor + PWA + verificación
#   ./deploy-all.sh --agent       solo el agente
#   ./deploy-all.sh --executor    solo el ejecutor
#   ./deploy-all.sh --pwa         solo la PWA (no necesita sudo)
#   ./deploy-all.sh --tools       solo jarvis-tools (índice NAS, lecturas)
#   ./deploy-all.sh --filebrowser solo FileBrowser (config + tema, reinicio)
#   ./deploy-all.sh --check       solo verifica, sin tocar nada (no usa sudo)
#   ./deploy-all.sh --test        tests del agente + PWA y luego --full
#
# Requisitos: ssh con clave, rsync, npm. En la placa: docker compose y curl.
#
# Variables opcionales:
#   JARVIS_BOARD        (por defecto leandro@100.111.200.54)
#   JARVIS_SSH_KEY      (por defecto ~/.ssh/id_ed25519_jarvis)
#   JARVIS_REMOTE_DIR   (por defecto /home/leandro/jarvis)
# =============================================================================
set -euo pipefail

BOARD="${JARVIS_BOARD:-leandro@100.111.200.54}"
SSH_KEY="${JARVIS_SSH_KEY:-$HOME/.ssh/id_ed25519_jarvis}"
REMOTE_DIR="${JARVIS_REMOTE_DIR:-/home/leandro/jarvis}"
HEALTH_LOCAL="http://127.0.0.1:8888/health"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_OPTS=(-i "$SSH_KEY" -o ConnectTimeout=8)
RSYNC_SSH="ssh -i $SSH_KEY -o BatchMode=yes"

rojo() { printf '\033[31m%s\033[0m\n' "$*"; }
verde() { printf '\033[32m%s\033[0m\n' "$*"; }
ambar() { printf '\033[33m%s\033[0m\n' "$*"; }
paso() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# --- SSH sin TTY: solo lectura / verificación ------------------------------
remote() { ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$BOARD" "$@"; }

# --- SSH con TTY: es el único modo en que sudo puede preguntar -------------
remote_sudo() {
  if [ ! -t 0 ]; then
    rojo "✗ Esta acción necesita sudo en la placa y no tengo terminal interactiva."
    echo "  Ejecútala desde tu terminal (Terminal.app / iTerm2), no por un pipe"
    echo "  ni desde un agente: sudo necesita la terminal para pedir la clave."
    exit 2
  fi
  ssh "${SSH_OPTS[@]}" -t "$BOARD" "$1"
}

# --- El commit que se despliega se explica solo -----------------------------
# Se avisa, no se bloquea: el commit ya existe y reescribir historia para
# arreglar un mensaje es peor que desplegar con un aviso. La puerta de verdad
# está en el hook de commit (scripts/git-hooks/commit-msg).
revisar_commit() {
  [ -x "$HERE/scripts/check-commit.sh" ] || return 0
  local salida
  if ! salida="$("$HERE/scripts/check-commit.sh" --last 2>&1)"; then
    ambar "· el último commit no sigue la convención (aviso, no bloqueo):"
    printf '%s\n' "$salida" | head -8 | sed 's/^/    /'
  fi
}

# --- El disco de datos debe estar vivo antes de gastar un build ------------
# El agente, el NAS y las copias viven en /mnt/data. Si el USB se desenganchó
# (pasó el 2026-10-22), los contenedores no pueden recrearse y un deploy
# "exitoso" dejaría la mitad de la infraestructura caída. Detector temprano.
previa_disco() {
  paso "0/4 verificando el disco de datos"
  local salida
  salida="$(remote \
    "mountpoint -q /mnt/data && touch /mnt/data/.prueba-deploy && rm /mnt/data/.prueba-deploy && echo OK" \
    || true)"
  if [ "$salida" != "OK" ]; then
    rojo "✗ /mnt/data no responde a escritura (disco USB desenganchado/colgado)"
    echo "  Arreglo: ssh -t $BOARD 'sudo bash $REMOTE_DIR/deploy/remote/montar-datos.sh'"
    exit 1
  fi
  verde "✔ /mnt/data vivo"
}

# --- Sincronizar código (sin sudo: /home/leandro/jarvis es tuyo) -----------
sincronizar_codigo() {
  revisar_commit
  paso "1/4 sincronizando código a $BOARD"
  rsync -az -e "$RSYNC_SSH" \
    "$HERE/agent/app/" "$BOARD:$REMOTE_DIR/agent/app/"
  rsync -az -e "$RSYNC_SSH" \
    "$HERE/agent/tests/" "$BOARD:$REMOTE_DIR/agent/tests/"
  # El requirements del agente define lo que se instala en la imagen:
  # sin sincronizarlo, Docker reutiliza la capa vieja y el agente arranca
  # sin dependencias nuevas (pasa con python-multipart el 2026-10-22).
  rsync -az -e "$RSYNC_SSH" \
    "$HERE/agent/requirements.txt" "$HERE/agent/Dockerfile" \
    "$BOARD:$REMOTE_DIR/agent/"
  rsync -az -e "$RSYNC_SSH" \
    "$HERE/executor/app/" "$BOARD:$REMOTE_DIR/executor/app/"
  rsync -az -e "$RSYNC_SSH" \
    "$HERE/tools/app/" "$BOARD:$REMOTE_DIR/tools/app/"
  rsync -az -e "$RSYNC_SSH" \
    "$HERE/tools/Dockerfile" "$HERE/tools/requirements.txt" \
    "$BOARD:$REMOTE_DIR/tools/"
  rsync -az -e "$RSYNC_SSH" \
    "$HERE/deploy/remote/" "$BOARD:$REMOTE_DIR/deploy/remote/"
  rsync -az -e "$RSYNC_SSH" \
    "$HERE/docker-compose.yml" "$BOARD:$REMOTE_DIR/docker-compose.yml"
  # FileBrowser guarda su config y su tema en el disco de datos (no en el
  # repo), pero el repo manda: se sincronizan en cada despliegue para que el
  # "solo lectura + sin login + tema Jarvis" no se pierda al reinstalar.
  rsync -az -e "$RSYNC_SSH" \
    "$HERE/deploy/filebrowser/config.yaml" "$HERE/deploy/filebrowser/jarvis.css" \
    "$BOARD:/mnt/data/docker/filebrowser/data/"
  verde "✔ código sincronizado"
}

# --- Bloques remotos con sudo (una sola contraseña por bloque) ------------
# Todo el trabajo con sudo ocurre dentro de deploy/remote/rebuild.sh, que se
# ejecuta en la placa y no vuelve hasta que los servicios están SANOS.
reconstruir() {
  local servicios="$1" descripcion="$2"
  paso "$descripcion (sudo una vez)"
  remote_sudo "sudo bash $REMOTE_DIR/deploy/remote/rebuild.sh $servicios"
}

reconstruir_agente() { reconstruir "agente" "reconstruyendo jarvis-agent"; }
reconstruir_ejecutor() { reconstruir "ejecutor" "reconstruyendo jarvis-executor"; }
reconstruir_herramientas() { reconstruir "herramientas" "reconstruyendo jarvis-tools"; }
reconstruir_ambos() { reconstruir "agente ejecutor herramientas" "2/4 reconstruyendo agente + ejecutor + tools"; }

deploy_pwa() {
  paso "3/4 compilando y publicando la PWA"
  (cd "$HERE/pwa" && ./deploy.sh)
}

# --- Verificación de extremo a extremo (sin sudo) -------------------------
verificar() {
  paso "4/4 verificando"
  local fallos=0 health asset_local asset_servido files_code
  health="$(remote "curl -sS -f --max-time 8 $HEALTH_LOCAL" || true)"
  if [ -z "$health" ]; then
    rojo "✗ /health no responde"
    return 1
  fi
  python3 - "$health" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
print(f"  versión        : {d.get('version')}")
print(f"  modelo LLM     : {d.get('llm_model')}")
print(f"  herramientas   : {d.get('tools_reachable')}")
print(f"  PWA servida    : {d.get('static_asset')}")
PY
  asset_servido="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("static_asset") or "")' "$health")"
  asset_local="$(grep -oE 'index-[A-Za-z0-9_-]+\.js' "$HERE/agent/static/index.html" 2>/dev/null | head -1 || true)"
  echo "  PWA local      : ${asset_local:-<sin build>}"
  if [ -n "$asset_local" ] && [ "$asset_local" != "$asset_servido" ]; then
    rojo "  ✗ /health sirve otro bundle que el build local"
    fallos=1
  fi

  # El proxy de FileBrowser se comprueba desde la placa: el token no sale de ahí.
  files_code="$(remote bash -s <<REMOTE || true
TOKEN=\$(grep -m1 '^JARVIS_API_TOKEN=' $REMOTE_DIR/.env | cut -d= -f2-)
curl -s -o /dev/null -w '%{http_code}' -H "X-API-Token: \$TOKEN" http://127.0.0.1:8888/files/
REMOTE
)"
  if [ "$files_code" = "200" ]; then
    verde "  ✔ proxy /files/ responde 200 (FileBrowser alcanzable)"
  else
    rojo "  ✗ proxy /files/ respondió '$files_code' (¿jarvis-agent recreado?)"
    fallos=1
  fi

  # /equipos es el endpoint que más veces se ha roto (500 del ejecutor con la
  # causa escondida). Se comprueba aquí para que no vuelva a pasar en silencio.
  local equipos
  equipos="$(remote bash -s <<REMOTE || true
TOKEN=\$(grep -m1 '^JARVIS_API_TOKEN=' $REMOTE_DIR/.env | cut -d= -f2-)
curl -s -H "X-API-Token: \$TOKEN" http://127.0.0.1:8888/api/equipos
REMOTE
)"
  if python3 - "$equipos" <<'PY'
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    print("  ✗ /equipos no devolvió JSON")
    raise SystemExit(1)
fallo = False
for h in d.get("hosts", []):
    estado = "online" if h.get("online") else "offline"
    print(f"  equipos: {h.get('id'):<10} {estado:<8} {h.get('reason')}")
    if "HTTP 5" in str(h.get("reason") or ""):
        fallo = True
raise SystemExit(1 if fallo else 0)
PY
  then
    :
  else
    rojo "  ✗ /equipos devuelve un error 5xx del ejecutor"
    fallos=1
  fi

  return $fallos
}

tests() {
  paso "tests del agente"
  (cd "$HERE/agent" && python3 -m pytest -q)
  paso "tests de la PWA"
  (cd "$HERE/pwa" && npm run test --silent)
}

case "${1:-}" in
  --check)
    verificar && verde "✔ verificación correcta"
    ;;
  --pwa)
    deploy_pwa
    ;;
  --agent)
    previa_disco
    sincronizar_codigo
    reconstruir_agente
    # El agente y su PWA se publican juntos: desplegar solo el backend dejó
    # una vez la interfaz vieja servida sin que nadie lo notara.
    deploy_pwa
    verificar && verde "✔ desplegado y verificado"
    ;;
  --tools)
    previa_disco
    sincronizar_codigo
    reconstruir_herramientas
    verificar && verde "✔ herramientas desplegadas y verificadas"
    ;;
  --filebrowser)
    previa_disco
    sincronizar_codigo
    reconstruir "archivos" "actualizando FileBrowser (config + tema)"
    verificar && verde "✔ FileBrowser actualizado y verificado"
    ;;
  --executor)
    previa_disco
    sincronizar_codigo
    reconstruir_ejecutor
    verificar && verde "✔ ejecutor desplegado y verificado"
    ;;
  --test)
    tests
    previa_disco
    sincronizar_codigo
    reconstruir_ambos
    deploy_pwa
    verificar && verde "✔ desplegado y verificado"
    ;;
  --full|"")
    previa_disco
    sincronizar_codigo
    reconstruir_ambos
    deploy_pwa
    verificar && verde "✔ desplegado y verificado"
    ;;
  *)
    rojo "opción desconocida: $1"
    echo "uso: $0 [--full|--agent|--executor|--pwa|--tools|--filebrowser|--check|--test]"
    exit 2
    ;;
esac
