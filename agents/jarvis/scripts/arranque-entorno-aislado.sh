#!/usr/bin/env bash
# Arranque del entorno AISLADO de la prueba integrada C5 (se ejecuta en el
# board). Todo en loopback: no se abre ningun puerto en la tailnet.
set -uo pipefail

PORT_EJECUTOR=8001
PORT_AGENTE=8889
TOKEN_EJECUTOR="$1"
TOKEN_APROB="$2"
TOKEN_API="$3"
HASH="$4"

pkill -f "uvicorn app.main:app --host 127.0.0.1 --port $PORT_EJECUTOR" 2>/dev/null || true
pkill -f "uvicorn app.main:app --host 127.0.0.1 --port $PORT_AGENTE" 2>/dev/null || true
rm -f /tmp/jarvis-c1/integrado-audit.db* /tmp/jarvis-c2/data/jarvis.db*
mkdir -p /tmp/jarvis-c2/data

cd /tmp/jarvis-c1
JARVIS_EXECUTOR_CATALOG_FILE=/tmp/jarvis-c1/config/catalog.yml \
JARVIS_EXECUTOR_HOSTS_FILE=/tmp/jarvis-c1/hosts-integrado.yml \
JARVIS_EXECUTOR_AUDIT_DB=/tmp/jarvis-c1/integrado-audit.db \
JARVIS_EXECUTOR_TOKEN="$TOKEN_EJECUTOR" \
JARVIS_EXECUTOR_APPROVALS_TOKEN="$TOKEN_APROB" \
setsid python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT_EJECUTOR" \
  > /tmp/jarvis-c1/ejecutor.log 2>&1 < /dev/null &

sleep 3
cd /tmp/jarvis-c2/agent
JARVIS_DATA_DIR=/tmp/jarvis-c2/data \
JARVIS_STATIC_DIR=/tmp/jarvis-c2/static \
JARVIS_API_TOKEN="$TOKEN_API" \
JARVIS_APPROVALS_TOKEN="$TOKEN_APROB" \
JARVIS_EXECUTOR_URL="http://127.0.0.1:$PORT_EJECUTOR" \
JARVIS_EXECUTOR_TOKEN="$TOKEN_EJECUTOR" \
JARVIS_ADMIN_PASSWORD_HASH="$HASH" \
JARVIS_COOKIE_SECURE=false \
PYTHONPATH=/tmp/jarvis-c2/pylibs \
setsid python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT_AGENTE" \
  > /tmp/jarvis-c2/agente.log 2>&1 < /dev/null &

sleep 5
echo "ejecutor: $(curl -s --max-time 5 http://127.0.0.1:$PORT_EJECUTOR/health)"
echo "agente:   $(curl -s --max-time 5 http://127.0.0.1:$PORT_AGENTE/health)"
