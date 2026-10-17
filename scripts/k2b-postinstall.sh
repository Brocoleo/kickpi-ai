#!/usr/bin/env bash
# Fase 6 — puesta a punto de la K2B V2 ya arrancada desde la eMMC.
# Se ejecuta EN LA PLACA (por SSH):  sudo bash k2b-postinstall.sh [opciones]
#
# Qué hace (idempotente, se puede repetir):
#   1. Comprueba lo esencial: raíz en la eMMC, wlan0 con IP, salida a Internet.
#   2. Zona horaria y hostname (si hace falta).
#   3. Actualiza el sistema (apt update + full-upgrade).
#   4. Cortafuegos ufw: permite SSH, deniega el resto de entradas, lo activa.
#   5. fail2ban para SSH (con un jail sensato).
#   6. Opcional (--docker): Docker + docker compose y el usuario en el grupo docker.
#   7. Resumen final con IP, kernel y estado de los servicios.
#
# Opciones:
#   --no-upgrade   no actualiza paquetes
#   --docker       instala docker.io + docker-compose y añade el usuario al grupo
#   --no-ufw       no toca el cortafuegos
#   --no-fail2ban  no instala/protege SSH
set -euo pipefail

TZ_WANT="${K2B_TZ:-America/Santiago}"
# OJO: 'HOSTNAME' la define bash sola (equipo actual) -> usamos K2B_HOSTNAME
HOST_WANT="${K2B_HOSTNAME:-kickpi-k2b-v2}"
DO_UPGRADE=1
DO_UFW=1
DO_F2B=1
DO_DOCKER=0

for a in "$@"; do
	case "$a" in
		--no-upgrade)  DO_UPGRADE=0 ;;
		--docker)      DO_DOCKER=1 ;;
		--no-ufw)      DO_UFW=0 ;;
		--no-fail2ban) DO_F2B=0 ;;
		-h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
		*) echo "opción desconocida: $a" >&2; exit 1 ;;
	esac
done

log(){ printf '\n==> %s\n' "$*"; }
warn(){ printf '    [aviso] %s\n' "$*"; }
die(){ printf 'error: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "ejecuta con sudo"
ADMIN="$(getent passwd 1000 | cut -d: -f1 || true)"
[[ -n "$ADMIN" ]] || die "no encuentro el usuario administrador (uid 1000)"

export DEBIAN_FRONTEND=noninteractive

# ------------------------------------------------------------------ 1. estado
log "Estado del sistema"
echo "    modelo : $(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo '?')"
echo "    kernel : $(uname -r)"
ROOT_SRC="$(findmnt -n -o SOURCE /)"
echo "    raíz   : $ROOT_SRC ($(findmnt -n -o FSTYPE /), $(df -h --output=size / | tail -1 | xargs))"
case "$(lsblk -nro PKNAME "$ROOT_SRC" 2>/dev/null | head -1)" in
	mmcblk2) echo "    [ OK ]  arrancando desde la eMMC" ;;
	mmcblk0) warn "estás arrancando desde la SD, no desde la eMMC" ;;
	*)       warn "dispositivo de arranque inesperado" ;;
esac

log "Red inalámbrica"
IFACE="$(ls /sys/class/net | grep -E '^wl' | head -1 || true)"
if [[ -z "$IFACE" ]]; then
	warn "no hay interfaz wlan; revisa el firmware/modulos del SWT6621S"
else
	ip -brief addr show "$IFACE" | sed 's/^/    /'
	if lsmod | grep -q swt6621s_wifi; then echo "    [ OK ]  módulo swt6621s_wifi cargado"
	else warn "el módulo swt6621s_wifi no está cargado"; fi
fi
if ping -c1 -W4 1.1.1.1 >/dev/null 2>&1; then echo "    [ OK ]  hay salida a Internet"
else warn "sin salida a Internet (¿el hotspot está encendido?)"; fi

# ------------------------------------------------ 2. hora y nombre de equipo
log "Hora y nombre de equipo"
timedatectl set-timezone "$TZ_WANT" 2>/dev/null || warn "no pude fijar la zona horaria"
[[ "$(cat /etc/hostname)" == "$HOST_WANT" ]] || hostnamectl set-hostname "$HOST_WANT" 2>/dev/null || warn "no pude fijar el hostname"
grep -qE "^127\.0\.1\.1[[:space:]]+$HOST_WANT" /etc/hosts || echo "127.0.1.1 $HOST_WANT" >> /etc/hosts
echo "    $(date '+%F %T %Z')  $(hostname)"

# ------------------------------------------------------------ 3. actualización
if [[ "$DO_UPGRADE" -eq 1 ]]; then
	log "Actualizando el sistema (puede tardar)"
	apt-get update -qq
	apt-get -y -o Dpkg::Options::=--force-confold full-upgrade
	apt-get -y autoremove --purge
	apt-get -y autoclean
else
	log "Actualización omitida (--no-upgrade)"
fi

# ---------------------------------------------------------------- 4. cortafuegos
if [[ "$DO_UFW" -eq 1 ]]; then
	log "Cortafuegos (ufw)"
	apt-get install -y -qq ufw
	ufw default deny incoming >/dev/null
	ufw default allow outgoing >/dev/null
	ufw allow 22/tcp comment 'ssh' >/dev/null
	ufw --force enable >/dev/null
	ufw status verbose | sed 's/^/    /'
else
	log "Cortafuegos omitido (--no-ufw)"
fi

# ------------------------------------------------------------------ 5. fail2ban
if [[ "$DO_F2B" -eq 1 ]]; then
	log "fail2ban (protección de SSH)"
	apt-get install -y -qq fail2ban
	cat > /etc/fail2ban/jail.d/k2b-sshd.local <<'EOF'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
backend = systemd
EOF
	systemctl enable --now fail2ban >/dev/null 2>&1 || warn "no pude habilitar fail2ban"
	systemctl is-active --quiet fail2ban && echo "    [ OK ]  fail2ban activo" || warn "fail2ban no está activo"
else
	log "fail2ban omitido (--no-fail2ban)"
fi

# -------------------------------------------------------------------- 6. docker
if [[ "$DO_DOCKER" -eq 1 ]]; then
	log "Docker"
	if ! command -v docker >/dev/null 2>&1; then
		apt-get install -y -qq docker.io docker-compose
	fi
	systemctl enable --now docker >/dev/null 2>&1 || warn "no pude habilitar docker"
	usermod -aG docker "$ADMIN" || warn "no pude añadir $ADMIN al grupo docker"
	docker --version | sed 's/^/    /'
	echo "    (cierra la sesión SSH y vuelve a entrar para usar docker sin sudo)"
else
	log "Docker omitido (usa --docker para instalarlo)"
fi

# ------------------------------------------------------------------ 7. resumen
log "Resumen"
cat <<EOF
    equipo   : $(hostname)  ($(date '+%F %T %Z'))
    kernel   : $(uname -r)
    IP       : $(ip -brief addr show "$IFACE" 2>/dev/null | awk '{print $3}' | head -1)
    raíz     : $ROOT_SRC  $(df -h --output=avail / | tail -1 | xargs) libres
    ssh      : $(systemctl is-active ssh 2>/dev/null || echo '?')
    ufw      : $(ufw status 2>/dev/null | head -1 || echo 'no instalado')
    fail2ban : $(systemctl is-active fail2ban 2>/dev/null || echo 'no instalado')
    docker   : $(command -v docker >/dev/null 2>&1 && docker --version || echo 'no instalado')
EOF
echo
echo "==> Listo."
