#!/usr/bin/env bash
# ============================================================================
# Establece la contraseña de navegador de Jarvis (PWA).
#
# Uso (en la KICKPI):
#   cd ~/jarvis && ./set-admin-password.sh
#
# La contraseña se pide de forma interactiva y NUNCA viaja por argv, chats
# ni historiales. En .env solo se guarda su hash scrypt.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "No existe .env en $(pwd)" >&2
  exit 1
fi

read -rsp "Nueva contraseña de Jarvis: " pw
echo
read -rsp "Repítela: " pw2
echo

if [[ -z "$pw" || "$pw" != "$pw2" ]]; then
  echo "Error: la contraseña está vacía o no coincide." >&2
  exit 1
fi

hash=$(printf '%s' "$pw" | python3 -c '
import hashlib, secrets, sys
pw = sys.stdin.buffer.read()
salt = secrets.token_bytes(16)
key = hashlib.scrypt(pw, salt=salt, n=2**14, r=8, p=1, dklen=32)
print(f"scrypt:16384:8:1:{salt.hex()}:{key.hex()}")
')
unset pw pw2

if grep -q '^JARVIS_ADMIN_PASSWORD_HASH=' .env; then
  sed -i "s|^JARVIS_ADMIN_PASSWORD_HASH=.*|JARVIS_ADMIN_PASSWORD_HASH=$hash|" .env
else
  printf '\nJARVIS_ADMIN_PASSWORD_HASH=%s\n' "$hash" >> .env
fi
unset hash

echo
echo "Ahora sudo pedirá la contraseña del SISTEMA (la del usuario leandro),"
echo "que es distinta de la contraseña de Jarvis que acabas de escribir."
echo

if docker info >/dev/null 2>&1; then
  docker compose up -d jarvis-agent
elif ! sudo docker compose up -d jarvis-agent; then
  echo
  echo "No se pudo reiniciar el agente (sudo falló)." >&2
  echo "El hash YA está guardado en .env; solo falta reiniciar:" >&2
  echo "  cd ~/jarvis && sudo docker compose up -d jarvis-agent" >&2
  exit 1
fi

sleep 4
if curl -fsS http://127.0.0.1:8888/health >/dev/null 2>&1; then
  echo "Agente reiniciado y respondiendo."
else
  echo "El agente aún está arrancando. Si no responde, revisa:"
  echo "  sudo docker compose logs -f jarvis-agent"
fi

echo
echo "Listo. Entra con tu nueva contraseña en:"
echo "  https://kickpi-jarvis.tail1e3b4d.ts.net:8888/"
