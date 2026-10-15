#!/usr/bin/env bash
# K2B V2 — Setup inicial TODO-EN-UNO (Fase 7). Idempotente y re-ejecutable siempre:
#   sudo bash /root/k2b-setup.sh
# Opcionales: HOSTNAME=kickpi-k2b TZ=America/Santiago WIFI_SSID="..." WIFI_PASS="..." SKIP_UPGRADE=1
# Sin red: omite apt/ufw con aviso y completa el resto (salud). Con red: hace todo.
set -u
HOSTNAME="${HOSTNAME:-kickpi-k2b}"
TZ="${TZ:-America/Santiago}"
SKIP_UPGRADE="${SKIP_UPGRADE:-0}"

[ "$(id -u)" -eq 0 ] || { echo "Ejecuta con sudo."; exit 1; }
ADMIN="$(getent passwd 1000 | cut -d: -f1 || true)"
[ -n "$ADMIN" ] || { echo "No hay usuario uid 1000 (termina el setup inicial primero)."; exit 1; }
echo "== Admin: $ADMIN =="

echo "== Zona horaria $TZ =="
timedatectl set-timezone "$TZ" || echo "WARN: timedatectl falló"
timedatectl | head -n 5 || true

echo "== Hostname $HOSTNAME =="
hostnamectl set-hostname "$HOSTNAME" || echo "WARN: hostnamectl falló"
grep -q "$HOSTNAME" /etc/hosts || echo "127.0.1.1 $HOSTNAME" >> /etc/hosts

NET_OK=0
if getent hosts deb.debian.org >/dev/null 2>&1; then NET_OK=1; echo "== Red: con DNS =="; else echo "== Red: SIN DNS (omito apt/ufw, re-ejecuta con red) =="; fi
if command -v nmcli >/dev/null 2>&1; then
  nmcli device status || true
  if [ -n "${WIFI_SSID:-}" ]; then nmcli device wifi connect "$WIFI_SSID" password "${WIFI_PASS:-}" || echo "WARN: no se unió a $WIFI_SSID"; fi
else
  echo "(sin NetworkManager: red por systemd-networkd; WiFi se configura aparte)"
fi
ip -brief addr || ip addr | grep -E "^[0-9]|inet " || true

if [ "$SKIP_UPGRADE" = "1" ]; then
  echo "== Paquetes omitidos (SKIP_UPGRADE=1) =="
elif [ "$NET_OK" = "1" ]; then
  echo "== Paquetes =="
  apt-get update && apt-get upgrade -y || echo "WARN: apt falló, re-ejecuta con red"
else
  echo "== Paquetes omitidos (sin red) =="
fi

echo "== SSH seguro =="
mkdir -p /etc/ssh/sshd_config.d
printf 'PermitRootLogin no\nPasswordAuthentication yes\n' > /etc/ssh/sshd_config.d/10-k2b.conf
if sshd -t; then systemctl restart ssh 2>/dev/null || systemctl restart sshd; else echo "WARN: sshd -t falló"; fi
systemctl is-active ssh 2>/dev/null || systemctl is-active sshd 2>/dev/null || echo "WARN: ssh no activo"

if [ "$NET_OK" = "1" ]; then
  echo "== Firewall (sin cerrar SSH) =="
  if ! command -v ufw >/dev/null 2>&1; then apt-get install -y ufw || echo "WARN: ufw no instalable"; fi
  if command -v ufw >/dev/null 2>&1; then
    ufw allow OpenSSH >/dev/null 2>&1 || ufw allow 22 >/dev/null 2>&1 || true
    yes | ufw enable || true
    ufw status | head -n 10 || true
  fi
else
  echo "== Firewall omitido (sin red; re-ejecuta con red) =="
fi

echo "== Salud =="
free -h; echo; df -h / 2>/dev/null || true; echo
cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo "(sin sensor temp)"
uname -a; echo
echo "OK. Re-ejecutable siempre: sudo bash /root/k2b-setup.sh"
