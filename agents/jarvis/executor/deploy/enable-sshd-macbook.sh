#!/usr/bin/env bash
# ============================================================================
# Activar acceso SSH para Jarvis en el MacBook.
#
# EJECUTAR COMO (una sola vez, cuando lo autorices):
#     sudo bash ~/enable-sshd-jarvis.sh
#
# Que hace:
#   1. Instala openssh-server (no estaba: por eso no habia host keys).
#   2. Genera las host keys y activa el servicio ssh.
#   3. Crea un usuario de sistema DEDICADO 'jarvis' (sin sudo, sin shell
#      interactiva, con la clave del ejecutor y un forced command).
#   4. Da a 'jarvis' permiso de ATRAVESAR $HOME (--x) para llegar a las
#      rutas autorizadas, sin poder listar la carpeta personal.
#   5. Instala el forced command (filtro) y su allow-list.
#
# El forced command hace que la clave del ejecutor NO de shell: solo puede
# ejecutar el catalogo autorizado, dentro de las raices autorizadas.
# ============================================================================
set -euo pipefail

# --- Usuario original: no depender de $HOME, que bajo sudo puede cambiar ---
if [ -z "${SUDO_USER:-}" ]; then
  echo "ERROR: ejecuta este script con sudo (sudo bash ~/enable-sshd-jarvis.sh)" >&2
  exit 1
fi
ORIGINAL_USER="$SUDO_USER"
ORIGINAL_HOME="$(getent passwd "$ORIGINAL_USER" | cut -d: -f6 || true)"
if [ -z "$ORIGINAL_HOME" ] || [ "$ORIGINAL_HOME" = "/root" ]; then
  echo "ERROR: no se pudo resolver el home del usuario '$ORIGINAL_USER'" >&2
  exit 1
fi
if [ ! -d "$ORIGINAL_HOME" ]; then
  echo "ERROR: el home '$ORIGINAL_HOME' no existe" >&2
  exit 1
fi

WRAPPER_SRC="$ORIGINAL_HOME/.local/share/jarvis/jarvis-ssh-wrapper"
ALLOWLIST_SRC="$ORIGINAL_HOME/.local/share/jarvis/allowlist.json"
WRAPPER_DST=/usr/local/libexec/jarvis-ssh-wrapper
ALLOWLIST_DST=/home/jarvis/.local/share/jarvis/allowlist.json
KEY_COMMENT="jarvis-executor-macbook"

echo "=============================================================="
echo " Activando SSH en el MacBook para Jarvis"
echo "=============================================================="
echo "  usuario que ejecuta sudo : $ORIGINAL_USER"
echo "  home del usuario         : $ORIGINAL_HOME"

echo
echo "[1/8] Instalando openssh-server..."
if ! [ -x /usr/sbin/sshd ]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server
else
  echo "      ya instalado"
fi

echo "[2/8] Comprobando herramientas necesarias..."
if ! command -v setfacl >/dev/null 2>&1; then
  echo "      falta setfacl: instalando el paquete 'acl'..."
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq acl
fi
command -v setfacl >/dev/null 2>&1 || { echo "      ERROR: setfacl no disponible" >&2; exit 1; }
command -v getfacl >/dev/null 2>&1 || { echo "      ERROR: getfacl no disponible" >&2; exit 1; }
echo "      setfacl: $(command -v setfacl)"
echo "      getfacl: $(command -v getfacl)"

echo "[3/8] Generando host keys..."
ssh-keygen -A >/dev/null 2>&1 || true

echo "[4/8] Creando el usuario de sistema 'jarvis' (minimo privilegio)..."
if ! id jarvis >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/jarvis \
          --shell /bin/bash --comment "Jarvis executor (SSH)" jarvis
  echo "      creado"
else
  echo "      ya existia"
fi
# Verificacion explicita: jarvis NO debe estar en el grupo sudo.
if id -nG jarvis | tr ' ' '\n' | grep -qx sudo; then
  echo "      ERROR: jarvis esta en el grupo sudo. Abortando." >&2
  exit 1
fi
echo "      grupos de jarvis: $(id -nG jarvis) (sin 'sudo')"

echo "[5/8] Permitiendo SOLO elatraveso de \$HOME (no listar)..."
# El home suele ser drwxr-x---: sin esto el usuario jarvis no puede llegar a
# las raices autorizadas. El ACL da 'x' (atravesar) sin 'r' (listar) y sin
# tocar los permisos de grupo/otros.
setfacl -m u:jarvis:--x "$ORIGINAL_HOME"
echo "      $(getfacl -p "$ORIGINAL_HOME" 2>/dev/null | grep -E 'user:jarvis' || echo 'ACL aplicado')"

echo "[6/8] Instalando el forced command y el allow-list..."
if [ ! -f "$WRAPPER_SRC" ]; then
  echo "      ERROR: no encuentro el wrapper en $WRAPPER_SRC" >&2
  exit 1
fi
if [ ! -f "$ALLOWLIST_SRC" ]; then
  echo "      ERROR: no encuentro el allow-list en $ALLOWLIST_SRC" >&2
  exit 1
fi
install -o root -g root -m 0755 "$WRAPPER_SRC" "$WRAPPER_DST"
install -d -o jarvis -g jarvis -m 0750 /home/jarvis/.local/share/jarvis
install -o root -g jarvis -m 0640 "$ALLOWLIST_SRC" "$ALLOWLIST_DST"

echo "[7/8] Instalando la clave del ejecutor con command= y restrict..."
mkdir -p /home/jarvis/.ssh
chown jarvis:jarvis /home/jarvis/.ssh
chmod 700 /home/jarvis/.ssh
touch /home/jarvis/.ssh/authorized_keys
# Solo se conserva la clave de Jarvis (filtra por comentario).
grep -v "$KEY_COMMENT" /home/jarvis/.ssh/authorized_keys > /tmp/.jarvis_ak 2>/dev/null || true
KEYLINE=$(grep "$KEY_COMMENT" "$ORIGINAL_HOME/.ssh/authorized_keys" | head -1)
if [ -z "$KEYLINE" ]; then
  echo "      ERROR: no encuentro la clave '$KEY_COMMENT' en $ORIGINAL_HOME/.ssh/authorized_keys" >&2
  exit 1
fi
printf 'command="%s",restrict %s\n' "$WRAPPER_DST" "$KEYLINE" >> /tmp/.jarvis_ak
install -o jarvis -g jarvis -m 0600 /tmp/.jarvis_ak /home/jarvis/.ssh/authorized_keys
rm -f /tmp/.jarvis_ak
echo "      instalado"

echo "[8/8] Activando y arrancando ssh..."
systemctl enable ssh >/dev/null 2>&1 || true
systemctl restart ssh
sleep 2

echo
echo "=== VERIFICACIONES ==="
echo "  ssh activo:        $(systemctl is-active ssh)"
echo "  config valida:      $(sshd -t 2>&1 && echo 'sshd -t OK' || echo 'ERROR')"
echo "  listener en :22:    $(ss -lnt | grep -c ':22 ') "
echo "  jarvis sin sudo:    $(id -nG jarvis | tr ' ' '\n' | grep -qx sudo && echo 'MAL' || echo 'correcto')"
echo "  authorized_keys:    $(awk '{print $1}' /home/jarvis/.ssh/authorized_keys)"
echo "  ACL en el home:     $(getfacl -p "$ORIGINAL_HOME" 2>/dev/null | grep -E 'user:jarvis' | tr -s ' ')"
echo
echo "  Host key ed25519 (la que se fijara en known_hosts):"
awk '{print "    " $1, $2}' /etc/ssh/ssh_host_ed25519_key.pub
echo "  huella SHA256:"
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub | sed 's/^/    /'

echo
echo "=============================================================="
echo " Listo. Avisale a Jarvis para que:"
echo "  1. fije el known_hosts con esa host key"
echo "  2. ejecute la primera prueba real (host_status, git_status, tests)"
echo "=============================================================="
