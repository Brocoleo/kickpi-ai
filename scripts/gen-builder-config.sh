#!/usr/bin/env bash
# Genera la configuración no-interactiva para el builder pyavitz/debian-image-builder
# orientada a la KICKPI K2B V2 (Allwinner H618, WiFi Seekwave SWT6621S).
#
# Uso:
#   ./scripts/gen-builder-config.sh <directorio-del-builder>
#
# El builder (rama "feature") exige que userdata.txt lleve el marcador BUILDER=7.11.
# Este script escribe userdata.txt y regenera custom.txt / kernel.txt / mirror.txt
# / profile.txt usando las propias funciones del builder (así los marcadores
# internos CUSTOM / KERNEL_PREFERENCES / MIRROR_LIST / USER_PROFILE quedan correctos).
set -euo pipefail

BUILDER_DIR="${1:-.}"
if [[ ! -d "$BUILDER_DIR" ]]; then
	echo "error: no existe el directorio del builder: $BUILDER_DIR" >&2
	exit 1
fi
BUILDER_DIR="$(cd "$BUILDER_DIR" && pwd)"
cd "$BUILDER_DIR"

if [[ ! -f lib/function/config ]]; then
	echo "error: $BUILDER_DIR no parece ser pyavitz/debian-image-builder" >&2
	exit 1
fi

# ---------------------------------------------------------------- parámetros
# Marcadores de versión que el builder valida (no cambiar salvo que el
# builder suba de versión; en ese caso actualizar ambas variables).
BUILDER_REV="${BUILDER_REV:-7.11}"
PROFILE_REV="${PROFILE_REV:-1.6}"

# Cuenta de usuario creada en la imagen (USERACCT=0 -> se crea en build time)
USER_NAME="${USER_NAME:-Leandro}"
USER_LOGIN="${USER_LOGIN:-leandro}"
USER_PASSWORD="${USER_PASSWORD:-1321}"
# OJO: 'HOSTNAME' la define bash sola con el nombre del equipo donde corre el
# script (el portátil / el runner). Por eso el nombre de la placa se pide en
# K2B_HOSTNAME; así nunca se cuela el hostname de la máquina de compilación.
HOSTNAME="${K2B_HOSTNAME:-kickpi-k2b-v2}"

# Distribución
DISTRO="${DISTRO:-debian}"
DISTRO_VERSION="${DISTRO_VERSION:-trixie}"
NETWORKMANAGER="${NETWORKMANAGER:-1}"

# Kernel / U-Boot. La placa fuerza 6.12.109 y v2026.07 internamente
# (lib/boards/kickpik2b-v2 -> FORCE_LINUX_VERSION / FORCE_UBOOT_VERSION),
# así que estos valores son solo el respaldo que exige userdata.txt.
UBOOT_VERSION="${UBOOT_VERSION:-v2026.01}"
KERNEL_VERSION="${KERNEL_VERSION:-6.12.109}"
BUILD_VERSION="${BUILD_VERSION:-1}"

# Localización
LOCALE="${LOCALE:-en_US.UTF-8}"
LOCALE_LC_ALL="${LOCALE_LC_ALL:-C.UTF-8}"
TIMEZONE="${TIMEZONE:-America/Santiago}"
NAMESERVER1="${NAMESERVER1:-8.8.8.8}"
NAMESERVER2="${NAMESERVER2:-8.8.4.4}"

# Imagen
IMGSIZE="${IMGSIZE:-4096MB}"
# 0 = el builder no comprime; comprimimos nosotros tras personalizar la imagen.
COMPRESS_IMG="${COMPRESS_IMG:-0}"

USER_PKGS="${USER_PKGS:-htop libsensors-dev rsyslog}"

# ------------------------------------------------------------------- avisos
echo "==> Builder dir : $BUILDER_DIR"
echo "==> Marcador    : BUILDER=$BUILDER_REV  PROFILE=$PROFILE_REV"
echo "==> Distro      : $DISTRO $DISTRO_VERSION  (NetworkManager=$NETWORKMANAGER)"
echo "==> Kernel      : $KERNEL_VERSION (forzado por la placa)"
echo "==> Usuario     : $USER_LOGIN / host $HOSTNAME"
echo "==> Timezone    : $TIMEZONE"
echo "==> IMGSIZE     : $IMGSIZE (COMPRESS_IMG=$COMPRESS_IMG)"

# ------------------------------------------- custom.txt / kernel.txt / mirrors
export BUILDER="$BUILDER_REV"
export PROFILE="$PROFILE_REV"
# Las funciones del builder (kernel_txt) interpolan estas variables al generar
# los .txt; deben existir aunque estén vacías por el "set -u".
export BOARD="${BOARD:-kickpik2b-v2}"
export GIT_REPO="${GIT_REPO:-}"
export GIT_BRANCH="${GIT_BRANCH:-}"
# profile_txt() interpola estas otras (el fichero resultante es solo una
# plantilla de preferencias, pero deben estar definidas por el "set -u").
export MYNAME="${MYNAME:-$USER_NAME}"
export USER="${USER:-$USER_LOGIN}"
export PASSWORD="$USER_PASSWORD"
export VERSION="$KERNEL_VERSION"
export COMPILER="${COMPILER:-gcc}"
export CCACHE="${CCACHE:-1}"
export USERACCT="${USERACCT:-0}"
export USER_PKGS
# shellcheck disable=SC1091
source lib/function/config

custom_txt
kernel_txt
mirror_txt
profile_txt

# IMGSIZE depende de cada build -> lo fijamos aquí (custom.txt lo lee scripts/rootfs)
sed -i "s|^IMGSIZE=.*|IMGSIZE=\"${IMGSIZE}\"|" custom.txt
sed -i "s|^IMG_COMPRESSION=.*|IMG_COMPRESSION=\"xz\"|" custom.txt

# Garantizar el WiFi extra (drivers out-of-tree; incluye el bloque SWT6621S/vs6621s)
sed -i "s|^EXTRA_WIFI=.*|EXTRA_WIFI=\"true\"|" kernel.txt

# ------------------------------------------------ userdata.txt (no-interactivo)
# Formato: variables bash que el builder "source" directamente.
cat > userdata.txt <<EOF
### USER INFORMATION
NAME="${USER_NAME}"
USERNAME="${USER_LOGIN}"
PASSWORD="${USER_PASSWORD}"
HOSTNAME="${HOSTNAME}"

### DISTRIBUTION AND RELEASE
DISTRO="${DISTRO}"
DISTRO_VERSION="${DISTRO_VERSION}"
NETWORKMANAGER="${NETWORKMANAGER}"

### UBOOT AND LINUX KERNEL
UBOOT_VERSION="${UBOOT_VERSION}"
VERSION="${KERNEL_VERSION}"
BUILD_VERSION="${BUILD_VERSION}"
MENUCONFIG="0"
COMPILER="gcc"
CCACHE="1"

### CUSTOM
CUSTOM_DEFCONFIG="0"
MYCONFIG=""

### COMPILER TUNING
LOGGING="1"
VERBOSE="0"
COMPRESS_IMG="${COMPRESS_IMG}"
USCRIPTS="0"
USERACCT="0"

### WHOAMI AND HOST
ROOTPASSWD="0"
USER_PKGS="${USER_PKGS}"
CORES=\$(nproc)
CFLAGS=""
KBUSER="foghorn"
KBHOST="leghorn"

### LOCALES TIMEZONE AND NAMESERVERS
LOCALE="${LOCALE}"
LOCALE_LC_ALL="${LOCALE_LC_ALL}"
TIMEZONE="${TIMEZONE}"
NAMESERVER1="${NAMESERVER1}"
NAMESERVER2="${NAMESERVER2}"

### DO NOT EDIT BELOW THIS LINE
BUILDER=${BUILDER_REV}
EOF

echo
echo "==> Archivos generados:"
ls -l userdata.txt custom.txt kernel.txt mirror.txt profile.txt
echo
echo "--- userdata.txt ---"
cat userdata.txt
