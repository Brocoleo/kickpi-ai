#!/usr/bin/env bash
# ============================================================================
# Activar acceso SSH para Jarvis en el MacBook.
#
# ESTE SCRIPT DEBES EJECUTARLO TU en el MacBook (necesita sudo):
#     sudo bash ~/enable-sshd-jarvis.sh
#
# Que hace:
#   1. Instala openssh-server (no estaba: por eso no habia host keys).
#   2. Genera las host keys y activa el servicio ssh.
#   3. Crea un usuario de sistema DEDICADO 'jarvis' (sin sudo, sin shell
#      interactiva, con la clave del ejecutor y un forced command).
#   4. Da a 'jarvis' permiso de ATRAVESAR $HOME (--x) para llegar a las
#      rutas autorizadas, sin poder listar tu carpeta personal.
#   5. Instala el forced command (filtro) y su allow-list.
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
echo "[1/7] Instalando openssh-server..."
if ! command -v /usr/sbin/sshd >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server
else
  echo "      ya instalado"
fi

echo "[2/7] Generando host keys..."
ssh-keygen -A >/dev/null 2>&1 || true

echo "[3/7] Creando el usuario de sistema 'jarvis' (minimo privilegio)..."
if ! id jarvis >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/jarvis \
          --shell /bin/bash --comment "Jarvis executor (SSH)" jarvis
  echo "      creado"
else
  echo "      ya existia"
fi
# Verificacion explicita: jarvis NO debe estar en el grupo sudo.
if id -nG jarvis | tr ' ' '\n' | grep -qx sudo; then
  echo "      ERROR: jarvis esta en el grupo sudo. Abortando."
  exit 1
fi
echo "      grupos de jarvis: $(id -nG jarvis) (sin 'sudo')"

echo "[4/7] Permitiendo SOLO elatraveso de \$HOME (no listar)..."
# /home/leandro es drwxr-x---: sin esto el usuario jarvis no puede llegar a
# las raices autorizadas. El ACL da 'x' (atravesar) sin 'r' (listar) y sin
# tocar los permisos de grupo/otros.
setfacl -m u:jarvis:--x "$HOME"
echo "      $(getfacl -p "$HOME" 2>/dev/null | grep -E 'user:jarvis' || echo 'ACL aplicado')"

echo "[5/7] Instalando el forced command y el allow-list..."
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

echo "[6/7] Instalando la clave del ejecutor con command= y restrict..."
mkdir -p /home/jarvis/.ssh
chown jarvis:jarvis /home/jarvis/.ssh
chmod 700 /home/jarvis/.ssh
touch /home/jarvis/.ssh/authorized_keys
# Solo se conserva la clave de Jarvis (filtra por comentario).
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

echo "[7/7] Activando y arrancando ssh..."
systemctl enable ssh >/dev/null 2>&1 || true
systemctl restart ssh
sleep 2

echo
echo "=== VERIFICACIONES ==="
echo "  ssh activo:        $(systemctl is-active ssh)"
echo "  config valida:      $(sshd -t 2>&1 && echo 'sshd -t OK' || echo 'ERROR')"
echo "  puerto 22:          $(ss -lnt | grep -c ':22 ') listener(s)"
echo "  jarvis sin sudo:    $(id -nG jarvis | tr ' ' '\n' | grep -qx sudo && echo 'MAL' || echo 'correcto')"
echo "  authorized_keys:    $(awk '{print $1}' /home/jarvis/.ssh/authorized_keys)"
echo
echo "  Host key ed25519 (esta es la que se fijara en known_hosts):"
awk '{print "    " $1, $2}' /etc/ssh/ssh_host_ed25519_key.pub
echo "  huella SHA256:"
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub | sed 's/^/    /'

echo
echo "=============================================================="
echo " Listo. Avisale a Jarvis para que:"
echo "  1. fije el known_hosts con esa host key"
echo "  2. ejecute la primera prueba real (host_status, git_status, tests)"
echo "=============================================================="
