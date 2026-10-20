#!/usr/bin/env bash
# ============================================================================
# Prueba integrada C5 (AISLADA): PWA -> agente -> ejecutor -> ejecucion ->
# auditoria.
#
# NO toca produccion:
#   - bases de datos temporales
#   - configuracion temporal (hosts-integrado.yml)
#   - puertos de loopback en el board (8001 y 8889): inalcanzables desde la
#     tailnet; el MacBook entra por un tunel SSH
#   - el catalogo REAL (solo lectura)
#
# Con SSH=1 el host objetivo es el MacBook por SSH (requiere el disco
# externo montado: ahi vive la clave dedicada del ejecutor).
# ============================================================================
set -uo pipefail

BOARD="leandro@100.111.200.54"
KEY="$HOME/.ssh/id_ed25519_jarvis"
PORT_AGENTE=8889
PORT_EJECUTOR=8001
TOKEN_EJECUTOR="token-ejecutor-c5-$(date +%s)"
TOKEN_APROB="token-aprobaciones-c5-$(date +%s)"
TOKEN_API="token-api-c5-$(date +%s)"
PASSWORD="clave-integrada-c5"
SSH="${SSH:-0}"
REPO="/tmp/jarvis-c1/repo-integrado"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()  { printf '  \033[32mOK\033[0m %s\n' "$*"; }
ko()  { printf '  \033[31mFALLO\033[0m %s\n' "$*"; }

say "1. Sincronizando el codigo al board (copias aisladas)"
cd /home/leandro/kickpi-ai/agents/jarvis
tar cf - -C executor app tests config pytest.ini | ssh -i "$KEY" "$BOARD" 'cd /tmp/jarvis-c1 && tar xf -'
tar cf - -C agent app tests pytest.ini | ssh -i "$KEY" "$BOARD" 'mkdir -p /tmp/jarvis-c2/agent && cd /tmp/jarvis-c2/agent && tar xf -'
tar cf - -C agent static | ssh -i "$KEY" "$BOARD" 'rm -rf /tmp/jarvis-c2/static && cd /tmp/jarvis-c2 && tar xf -'
ok "codigo sincronizado"

say "2. Configuracion temporal del ejecutor"
if [ "$SSH" = "1" ]; then
  ssh -i "$KEY" "$BOARD" "cat > /tmp/jarvis-c1/hosts-integrado.yml" <<'YML'
hosts:
  - id: macbook
    label: MacBook Pro (Ubuntu 24.04)
    transport: ssh
    enabled: true
    address: 100.126.29.56
    port: 22
    user: jarvis
    identity_file: "/mnt/data/docker/executor_ssh/id_ed25519_macbook"
    known_hosts: "/mnt/data/docker/executor_ssh/known_hosts_macbook"
    roots:
      - /home/leandro/kickpi-ai
projects: []
YML
  TARGET_PATH="/home/leandro/kickpi-ai"
  ok "host remoto: macbook por SSH (forced command)"
else
  ssh -i "$KEY" "$BOARD" "cat > /tmp/jarvis-c1/hosts-integrado.yml" <<'YML'
hosts:
  - id: macbook
    label: MacBook Pro (contenedor de pruebas)
    transport: local
    enabled: true
    roots:
      - /tmp/jarvis-c1/repo-integrado
projects: []
YML
  TARGET_PATH="/tmp/jarvis-c1/repo-integrado"
  ok "host local de pruebas (repo git temporal en el board)"
fi

say "3. Repositorio git de pruebas (con un cambio sin commitear)"
ssh -i "$KEY" "$BOARD" "rm -rf $REPO && mkdir -p $REPO && cd $REPO && \
  git init -q && printf 'uno\n' > archivo.txt && git add archivo.txt && \
  git -c user.email=c5@jarvis.local -c user.name=c5 commit -q -m inicial && \
  printf 'uno\ndos\n' > archivo.txt && git --no-optional-locks diff --stat"
ok "repo listo"

say "4. Calculando el hash de la contrasena de prueba"
HASH=$(ssh -i "$KEY" "$BOARD" "cd /tmp/jarvis-c2/agent && PYTHONPATH=/tmp/jarvis-c2/pylibs python3 -c \"from app.security import hash_password; print(hash_password('$PASSWORD'))\"")

say "5. Arrancando el ejecutor y el agente (loopback, bases temporales)"
scp -i "$KEY" /tmp/opencode/c5_arranque.sh "$BOARD":/tmp/c5_arranque.sh
ssh -i "$KEY" "$BOARD" "chmod +x /tmp/c5_arranque.sh && /tmp/c5_arranque.sh '$TOKEN_EJECUTOR' '$TOKEN_APROB' '$TOKEN_API' '$HASH'"

# Espera activa: no se avanza hasta que ambos responden.
for i in $(seq 1 30); do
  EJ=$(ssh -i "$KEY" "$BOARD" "curl -s --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT_EJECUTOR/health")
  AG=$(ssh -i "$KEY" "$BOARD" "curl -s --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT_AGENTE/health")
  [ "$EJ" = "200" ] && [ "$AG" = "200" ] && break
  sleep 1
done
[ "$EJ" = "200" ] && [ "$AG" = "200" ] && ok "servicios listos" || { ko "los servicios no arrancaron"; exit 1; }

say "6. Tunel SSH desde el MacBook (no se abre ningun puerto en la tailnet)"
ssh -i "$KEY" -N -L 127.0.0.1:$PORT_AGENTE:127.0.0.1:$PORT_AGENTE "$BOARD" &
TUNNEL=$!
trap 'kill $TUNNEL 2>/dev/null || true' EXIT
sleep 2

say "7. El AGENTE pide la operacion (exactamente lo que haria el LLM)"
SALIDA=$(ssh -i "$KEY" "$BOARD" "cd /tmp/jarvis-c2/agent && PYTHONPATH=/tmp/jarvis-c2/pylibs \
  JARVIS_EXECUTOR_URL=http://127.0.0.1:$PORT_EJECUTOR \
  JARVIS_EXECUTOR_TOKEN='$TOKEN_EJECUTOR' \
  python3 -c \"
import asyncio, json
from app.executor_client import call_executor
resultado = asyncio.run(call_executor('equipos_ejecutar', {
    'host': 'macbook',
    'operation': 'host_git_diff_stat',
    'params': {'path': '$TARGET_PATH'},
}))
print(json.dumps(resultado))
\"")
echo "  $(echo "$SALIDA" | head -c 200)"
APPROVAL_ID=$(echo "$SALIDA" | python3 -c 'import json,sys; print(json.load(sys.stdin)["approval"]["id"])')
ok "solicitud pendiente creada: $APPROVAL_ID"

say "8. La PWA real (navegador) aprueba y ejecuta"
cd /home/leandro/kickpi-ai/agents/jarvis/pwa
if JARVIS_E2E_URL="http://127.0.0.1:$PORT_AGENTE" \
   JARVIS_E2E_PASSWORD="$PASSWORD" \
   JARVIS_E2E_PATH="$TARGET_PATH" \
   npx playwright test e2e/integrado.spec.ts --reporter=line; then
  ok "flujo PWA -> agente -> ejecutor -> ejecucion -> auditoria"
else
  ko "el flujo integrado fallo"
fi

say "9. Estado persistido y bloqueo de replay (consulta directa al ejecutor)"
ssh -i "$KEY" "$BOARD" "python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('/tmp/jarvis-c1/integrado-audit.db')
conn.row_factory = sqlite3.Row
print('  --- approvals ---')
for row in conn.execute('SELECT id, status, requested_by, approved_by, operation, exit_code, duration_ms FROM approvals'):
    print('   ', dict(row))
print('  --- executions (auditoria) ---')
for row in conn.execute('SELECT operation, allowed, ok, exit_code, substr(params_json,1,70) AS params FROM executions ORDER BY id'):
    print('   ', dict(row))
conn.close()
PY"

say "10. Replay: el backend lo bloquea (409, un solo uso)"
ssh -i "$KEY" "$BOARD" "curl -s -o /dev/null -w '  POST /approvals/$APPROVAL_ID/execute (2a vez) -> HTTP %{http_code}\n' \
  -X POST http://127.0.0.1:$PORT_EJECUTOR/approvals/$APPROVAL_ID/execute \
  -H 'X-Approvals-Token: $TOKEN_APROB'"

say "11. Apagando el entorno aislado"
ssh -i "$KEY" "$BOARD" "
  pkill -f 'uvicorn app.main:app --host 127.0.0.1 --port $PORT_EJECUTOR' 2>/dev/null || true
  pkill -f 'uvicorn app.main:app --host 127.0.0.1 --port $PORT_AGENTE' 2>/dev/null || true
  rm -f /tmp/jarvis-c1/integrado-audit.db* /tmp/jarvis-c2/data/jarvis.db*
"
ok "entorno aislado apagado y bases temporales borradas"
