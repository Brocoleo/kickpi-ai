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

echo "Hash guardado en .env. Reiniciando el agente…"
sudo docker compose up -d jarvis-agent
echo
echo "Listo. Entra con tu nueva contraseña en:"
echo "  https://kickpi-jarvis.tail1e3b4d.ts.net:8888/"
