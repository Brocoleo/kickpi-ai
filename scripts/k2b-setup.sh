#!/usr/bin/env bash
# K2B V2 — Setup inicial TODO-EN-UNO (Fase 7). Ejecutar UNA vez en la K2B:
#   sudo bash /root/k2b-setup.sh
# Opcionales por entorno (export antes o VAR=valor sudo -E bash ...):
#   HOSTNAME=kickpi-k2b TZ=America/Santiago WIFI_SSID="iPhone de Leandro" WIFI_PASS="..." SKIP_UPGRADE=1
set -euo pipefail
HOSTNAME="${HOSTNAME:-kickpi-k2b}"
TZ="${TZ:-America/Santiago}"
SKIP_UPGRADE="${SKIP_UPGRADE:-0}"

[ "$(id -u)" -eq 0 ] || { echo "Ejecuta con sudo."; exit 1; }
ADMIN="$(getent passwd 1000 | cut -d: -f1 || true)"
[ -n "$ADMIN" ] || { echo "No hay usuario uid 1000 (cancela: termina el setup inicial primero)."; exit 1; }
echo "== Admin detectado: $ADMIN =="

echo "== Zona horaria $TZ =="
timedatectl set-timezone "$TZ" || true
timedatectl | head -n 5

echo "== Hostname $HOSTNAME =="
hostnamectl set-hostname "$HOSTNAME" || true
grep -q "$HOSTNAME" /etc/hosts || echo "127.0.1.1 $HOSTNAME" >> /etc/hosts

echo "== Red =="
if [ -n "${WIFI_SSID:-}" ]; then
  nmcli device wifi connect "$WIFI_SSID" password "${WIFI_PASS:-}" || echo "WARN: no se pudo unir a $WIFI_SSID"
fi
nmcli device status || true
ip -brief addr
echo "== DNS =="
getent hosts deb.debian.org | head -n 2 || echo "WARN: sin DNS aún (normal sin red)"

if [ "$SKIP_UPGRADE" != "1" ]; then
  echo "== Paquetes (puede gastar datos del hotspot) =="
  apt-get update && apt-get upgrade -y
else
  echo "== Paquetes omitidos (SKIP_UPGRADE=1) =="
fi

echo "== SSH seguro =="
mkdir -p /etc/ssh/sshd_config.d
printf 'PermitRootLogin no\nPasswordAuthentication yes\n' > /etc/ssh/sshd_config.d/10-k2b.conf
sshd -t && systemctl restart ssh || systemctl restart sshd
systemctl is-active ssh || systemctl is-active sshd

echo "== Firewall (sin cerrar SSH) =="
if ! command -v ufw >/dev/null; then apt-get install -y ufw; fi
ufw allow OpenSSH >/dev/null || ufw allow 22 >/dev/null
yes | ufw enable || true
ufw status | head -n 10

echo "== Salud =="
free -h; echo; df -h / /boot 2>/dev/null || df -h /; echo
cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || true
uname -a
echo
echo "OK. Revisa lo anterior y luego: reboot (debe volver solo; la SD expande/persiste)."
