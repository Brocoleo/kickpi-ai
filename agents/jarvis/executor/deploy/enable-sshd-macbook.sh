#!/usr/bin/env bash
# ============================================================================
# Activar acceso SSH para Jarvis en el MacBook (para el MacBook unicamente).
#
# ESTE SCRIPT DEBES EJECUTARLO TU en el MacBook (necesita sudo):
#     sudo bash ~/enable-sshd-jarvis.sh
#
# Que hace:
#   1. Instala openssh-server (no estaba: por eso no habia host keys).
#   2. Genera las host keys y activa el servicio ssh.
#   3. Crea un usuario de sistema DEDICADO 'jarvis' (minimo privilegio).
#   4. Instala el forced command (filtro) y su allow-list.
#   5. Instala la clave del ejecutor con command= y restrict.
#
# El forced command hace que la clave del ejecutor NO de shell: solo puede
# ejecutar el catalogo autorizado, dentro de las raices autorizadas.
# ============================================================================
set -euo pipefail

WRAPPER_SRC="${WRAPPER_SRC:-$HOME/.local/share/jarvis/jarvis-ssh-wrapper}"
ALLOWLIST_SRC="${ALLOWLIST_SRC:-$HOME/.local/share/jarvis/allowlist.json}"
WRAPPER_DST=/usr/local/libexec/jarvis-ssh-wrapper
ALLOWLIST_DST=/home/jarvis/.local/share/jarvis/allowlist.json
KEY_COMMENT="jarvis-executor-macbook"

echo "=============================================================="
echo " Activando SSH en el MacBook para Jarvis"
echo "=============================================================="

echo
echo "[1/6] Instalando openssh-server..."
if ! command -v /usr/sbin/sshd >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server
else
  echo "      ya instalado"
fi

echo "[2/6] Generando host keys..."
ssh-keygen -A >/dev/null 2>&1 || true

echo "[3/6] Creando el usuario de sistema 'jarvis' (minimo privilegio)..."
if ! id jarvis >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/jarvis \
          --shell /bin/bash --comment "Jarvis executor (SSH)" jarvis
  echo "      creado"
else
  echo "      ya existia"
fi

echo "[4/6] Instalando el forced command y el allow-list..."
if [ ! -f "$WRAPPER_SRC" ]; then
  echo "      ERROR: no encuentro el wrapper en $WRAPPER_SRC"
  exit 1
fi
if [ ! -f "$ALLOWLIST_SRC" ]; then
  echo "      ERROR: no encuentro el allow-list en $ALLOWLIST_SRC"
  exit 1
fi
install -o root -g root -m 0755 "$WRAPPER_SRC" "$WRAPPER_DST"
install -d -o jarvis -g jarvis -m 0750 /home/jarvis/.local/share/jarvis
install -o root -g jarvis -m 0640 "$ALLOWLIST_SRC" "$ALLOWLIST_DST"

echo "[5/6] Instalando la clave del ejecutor con command= y restrict..."
mkdir -p /home/jarvis/.ssh
chown jarvis:jarvis /home/jarvis/.ssh
chmod 700 /home/jarvis/.ssh
touch /home/jarvis/.ssh/authorized_keys
# Conserva solo las claves que ya sean de Jarvis (filtra por comentario).
grep -v "$KEY_COMMENT" /home/jarvis/.ssh/authorized_keys > /tmp/.jarvis_ak 2>/dev/null || true
KEYLINE=$(grep "$KEY_COMMENT" "$HOME/.ssh/authorized_keys" | head -1)
if [ -z "$KEYLINE" ]; then
  echo "      ERROR: no encuentro la clave '$KEY_COMMENT' en $HOME/.ssh/authorized_keys"
  exit 1
fi
printf 'command="%s",restrict %s\n' "$WRAPPER_DST" "$KEYLINE" >> /tmp/.jarvis_ak
install -o jarvis -g jarvis -m 0600 /tmp/.jarvis_ak /home/jarvis/.ssh/authorized_keys
rm -f /tmp/.jarvis_ak
echo "      instalado"

echo "[6/6] Activando y arrancando ssh..."
systemctl enable ssh >/dev/null 2>&1 || true
systemctl restart ssh
sleep 2
systemctl is-active ssh

echo
echo "Comprobaciones:"
ss -lnt | grep -E ':22\b' || echo "  AVISO: el puerto 22 no aparece escuchando"
echo "  host key ed25519:"
awk '{print "    " $1, $2}' /etc/ssh/ssh_host_ed25519_key.pub

echo
echo "=============================================================="
echo " Listo. Avisale a Jarvis para que:"
echo "  1. fije el known_hosts con la host key de arriba"
echo "  2. cambie el usuario del ejecutor a 'jarvis'"
echo "  3. ejecute la primera prueba real contra el MacBook"
echo "=============================================================="
