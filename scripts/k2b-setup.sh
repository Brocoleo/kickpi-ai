#!/usr/bin/env bash
# K2B V2 — Setup TODO-EN-UNO v3 (WiFi + firmware + red + firewall).
# Pensado para ejecutarse UNA sola vez:
#     sudo bash /root/k2b-setup.sh
# Hace todo, se auto-reinicia si hace falta y deja al board conectado solo.
# Es idempotente: si lo vuelves a ejecutar, no rompe nada.
#
# Opcionales:
#   WIFI_SSID="iPhone de Leandro" WIFI_PASS="Danae2100" WIFI_COUNTRY=CL
#   HOSTNAME=kickpi-k2b-v2 TZ=America/Santiago SKIP_UPGRADE=1 NO_REBOOT=1
set -u

HOSTNAME="${HOSTNAME:-kickpi-k2b-v2}"
TZ="${TZ:-America/Santiago}"
WIFI_SSID="${WIFI_SSID:-iPhone de Leandro}"
WIFI_PASS="${WIFI_PASS:-Danae2100}"
WIFI_COUNTRY="${WIFI_COUNTRY:-CL}"
SKIP_UPGRADE="${SKIP_UPGRADE:-0}"
NO_REBOOT="${NO_REBOOT:-0}"

LOG=/root/k2b-setup.log
DIAG=/root/k2b-wifi-diag.txt
BOOT_MARK=/root/.k2b-reboot-once

mkdir -p /root
: >"$LOG"
# duplica todo a consola y a $LOG
exec > >(tee -a "$LOG") 2>&1

echo "====================================================="
echo " KICKPI K2B V2 — setup v3   $(date -Is)"
echo "====================================================="

[ "$(id -u)" -eq 0 ] || { echo "ERROR: ejecuta con sudo."; exit 1; }

ADMIN="$(getent passwd 1000 | cut -d: -f1 || true)"
[ -n "$ADMIN" ] || { echo "ERROR: no hay usuario uid 1000."; exit 1; }
echo "Usuario admin : $ADMIN"

fw_info() { echo "$(uname -r) / $(cat /proc/device-tree/model 2>/dev/null | tr -d '\0')"; }
echo "Sistema       : $(fw_info)"

# ------------------------------------------------------------------
# 1. Base del sistema (hora, hostname, hosts)
# ------------------------------------------------------------------
echo
echo "== [1/7] Base del sistema =="
timedatectl set-timezone "$TZ" 2>/dev/null || echo "  WARN: timedatectl fallo"
hostnamectl set-hostname "$HOSTNAME" 2>/dev/null || echo "  WARN: hostnamectl fallo"
grep -qE "127\.0\.1\.1[[:space:]]+$HOSTNAME" /etc/hosts \
    || echo "127.0.1.1 $HOSTNAME" >>/etc/hosts
echo "  hora   : $(date '+%F %T %Z')"
echo "  equipo : $(hostname)"

# ------------------------------------------------------------------
# 2. Firmware WiFi — enlaces para los nombres que el driver busca
#    La imagen trae los .bin con sufijo de otra placa (.kickpi,k3b.);
#    el driver pide el nombre "pelado" o con la compatible de esta placa.
#    Creamos symlinks (idempotente). No borra ni sobrescribe nada real.
# ------------------------------------------------------------------
echo
echo "== [2/7] Firmware WiFi (symlinks) =="
FW=/lib/firmware
SK="$FW/seekwave"
COMPAT="$(tr '\0' '\n' </proc/device-tree/compatible 2>/dev/null | head -n1)"

link_if() {  # $1=real  $2=enlace
    [ -e "$1" ] || return 1
    if [ -e "$2" ] || [ -L "$2" ]; then
        return 0
    fi
    ln -sfn "$1" "$2" && echo "  enlace $(basename "$2") -> $(basename "$1")"
}

fw_links() {
    local dir="$1" f base strip
    [ -d "$dir" ] || return 0
    for f in "$dir"/*; do
        [ -e "$f" ] || continue
        case "$(basename "$f")" in *".kickpi,"*)
            base="$(basename "$f")"
            # SWT6621S_DRAM_SDIO.kickpi,k3b.bin  ->  SWT6621S_DRAM_SDIO.bin
            strip="$(printf '%s' "$base" | sed -E 's/\.kickpi,[^.]+\././')"
            if [ "$strip" != "$base" ]; then
                link_if "$f" "$dir/$strip"
                [ "$dir" = "$SK" ] && link_if "$f" "$FW/$strip"
                # variante con la compatible de ESTA placa
                if [ -n "$COMPAT" ]; then
                    link_if "$f" "$dir/${strip%.*}${COMPAT:+.$COMPAT}.${strip##*.}"
                fi
            fi
            ;;
        esac
    done
}

if [ -d "$SK" ]; then
    echo "  compatible de la placa: ${COMPAT:-?}"
    fw_links "$SK"
    # alias conocidos de la familia SWT6621S / Seekwave
    link_if "$SK/SWT6621S_DRAM_SDIO.kickpi,k3b.bin" "$SK/SWT6621_DRAM_SDIO.bin"
    link_if "$SK/SWT6621S_IRAM_SDIO.kickpi,k3b.bin" "$SK/SWT6621_IRAM_SDIO.bin"
    link_if "$SK/SWT6621S_SEEKWAVE_R00001.kickpi,k3b.bin" "$SK/SWT6621S_SEEKWAVE_R00000.bin"
    link_if "$SK/SWT6621S_SEEKWAVE_R00001.kickpi,k3b.bin" "$SK/SEEKWAVE_NV_SWT6621S.bin"
    link_if "$SK/sv6160lite.kickpi,k3b.nvbin" "$SK/sv6160lite.nvbin"
else
    echo "  (no existe $SK)"
fi

echo "  contenido $SK:"
ls -1 "$SK" 2>/dev/null | sed 's/^/    /'

# ------------------------------------------------------------------
# 3. Módulos del WiFi — se cargan SIEMPRE al arrancar
#    El driver (Unisoc/Spreadtrum WCN) no trae autoload: hay que pedirlo.
# ------------------------------------------------------------------
echo
echo "== [3/7] Módulos WiFi =="
mkdir -p /etc/modules-load.d
cat >/etc/modules-load.d/uwe5622.conf <<'EOF'
# KICKPI K2B V2 — WiFi Unisoc/Spreadtrum WCN
uwe5622_bsp_sdio
sprdwl_ng
EOF
echo "  escrito /etc/modules-load.d/uwe5622.conf"

for m in uwe5622_bsp_sdio sprdwl_ng; do
    if lsmod | grep -q "^${m} "; then
        echo "  $m: ya cargado"
    else
        if modprobe "$m" 2>&1 | sed 's/^/    /'; then
            echo "  $m: cargado"
        else
            echo "  $m: FALLO modprobe"
        fi
    fi
done
# Bluetooth (no critico)
modprobe sprdbt_tty 2>/dev/null || true

# ------------------------------------------------------------------
# 4. Esperar la interfaz WiFi
# ------------------------------------------------------------------
find_wlan() {
    local d
    for d in /sys/class/net/wlan*; do
        [ -e "$d" ] && { basename "$d"; return 0; }
    done
    return 1
}

echo
echo "== [4/7] Esperando interfaz WiFi =="
IFACE=""
for _ in $(seq 1 40); do
    IFACE="$(find_wlan || true)"
    [ -n "$IFACE" ] && break
    sleep 1
done
if [ -n "$IFACE" ]; then
    echo "  interfaz: $IFACE"
    rfkill unblock all 2>/dev/null || true
else
    echo "  NO aparecio ninguna wlan* tras 40s"
fi

# ------------------------------------------------------------------
# 5. Configuración WiFi (wpa_supplicant + systemd-networkd)
# ------------------------------------------------------------------
echo
echo "== [5/7] Configurando WiFi =="
setup_wifi() {
    local iface="$1"
    local conf="/etc/wpa_supplicant/wpa_supplicant-${iface}.conf"
    mkdir -p /etc/wpa_supplicant /etc/systemd/network

    cat >"$conf" <<EOF
ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev
update_config=1
country=${WIFI_COUNTRY}

network={
    ssid="${WIFI_SSID}"
    psk="${WIFI_PASS}"
    key_mgmt=WPA-PSK
}
EOF
    chmod 600 "$conf"
    echo "  $conf escrito"

    cat >"/etc/systemd/network/25-${iface}.network" <<EOF
[Match]
Name=${iface}

[Network]
DHCP=ipv4
IPv6AcceptRA=yes
EOF
    echo "  /etc/systemd/network/25-${iface}.network escrito"

    # El servicio generico (D-Bus) molesta: usamos el template por interfaz.
    systemctl disable --now wpa_supplicant.service >/dev/null 2>&1 || true
    systemctl enable "wpa_supplicant@${iface}.service" >/dev/null 2>&1 || true
    systemctl restart systemd-networkd >/dev/null 2>&1 || true
    systemctl restart "wpa_supplicant@${iface}.service" >/dev/null 2>&1 || true
    echo "  servicios reiniciados"
}

if [ -n "$IFACE" ]; then
    setup_wifi "$IFACE"
else
    echo "  (sin interfaz: se aplicara automaticamente en el proximo arranque"
    echo "   via /etc/modules-load.d, wpa_supplicant@ y systemd-networkd)"
    # dejamos config lista para wlan0 por si aparece tras reiniciar
    setup_wifi wlan0 || true
fi

# ------------------------------------------------------------------
# 6. Terminar el setup (apt + firewall) cuando haya red
# ------------------------------------------------------------------
echo
echo "== [6/7] Servicio de cierre (apt + ufw) =="
cat >/usr/local/sbin/k2b-finish.sh <<'FIN'
#!/bin/bash
# Se ejecuta una vez, cuando la red esta arriba. Luego se autodesactiva.
set -u
LOG=/root/k2b-finish.log
exec >>"$LOG" 2>&1
echo "===== k2b-finish $(date -Is) ====="

# esperar red (hasta 120s)
for _ in $(seq 1 60); do
    getent hosts deb.debian.org >/dev/null 2>&1 && break
    sleep 2
done
if ! getent hosts deb.debian.org >/dev/null 2>&1; then
    echo "sin DNS tras 120s; se reintentara en el proximo arranque"
    exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get upgrade -y
apt-get install -y ufw

# firewall sin cortar SSH
ufw allow OpenSSH || ufw allow 22 || true
yes | ufw enable || true
ufw status | head -n 10 || true

echo "OK: apt + ufw completados $(date -Is)"
systemctl disable k2b-finish.service >/dev/null 2>&1 || true
FIN
chmod +x /usr/local/sbin/k2b-finish.sh

cat >/etc/systemd/system/k2b-finish.service <<'UNIT'
[Unit]
Description=K2B: terminar setup (apt + ufw) al tener red
Wants=network-online.target
After=network-online.target
ConditionPathExists=/usr/local/sbin/k2b-finish.sh

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/k2b-finish.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable k2b-finish.service >/dev/null 2>&1 && echo "  k2b-finish.service habilitado"

# ------------------------------------------------------------------
# 7. Diagnostico + estado + (auto)reinicio
# ------------------------------------------------------------------
echo
echo "== [7/7] Estado y diagnostico =="
IP=""
if [ -n "$IFACE" ]; then
    for _ in $(seq 1 45); do
        IP="$(ip -4 -o addr show dev "$IFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"
        [ -n "$IP" ] && break
        sleep 2
    done
fi

{
    echo "===== diagnostico $(date -Is) ====="
    echo "--- model/kernel"; fw_info
    echo "--- uname"; uname -a
    echo "--- interfaces"; ip -brief addr 2>/dev/null || ip addr
    echo "--- rutas"; ip route
    echo "--- rfkill"; rfkill list 2>/dev/null
    echo "--- lsmod (wifi/bt)"; lsmod | grep -iE "uwe|sprd|wcn|cfg80211|bluetooth" || echo "(ninguno)"
    echo "--- /sys/bus/sdio/devices"; ls /sys/bus/sdio/devices/ 2>/dev/null || echo "(nada)"
    echo "--- /sys/class/net"; ls /sys/class/net/
    echo "--- firmware uwe5622"; ls -l /lib/firmware/uwe5622/ 2>/dev/null
    echo "--- firmware seekwave"; ls -l /lib/firmware/seekwave/ 2>/dev/null
    echo "--- modulos-load"; cat /etc/modules-load.d/uwe5622.conf 2>/dev/null
    echo "--- wpa_supplicant@ status"; systemctl status "wpa_supplicant@${IFACE:-wlan0}.service" --no-pager 2>/dev/null | head -n 15
    echo "--- dmesg (wifi/mmc/wlan/fw)"; dmesg 2>/dev/null | grep -iE "uwe|sprd|wcn|wlan|sdio|mmc|firmware|SWT|seekwave|marlin" | tail -n 80
    echo "--- journal wifi (ultimo boot)"
    journalctl -b --no-pager 2>/dev/null | grep -iE "uwe|sprd|wcn|wlan|sdio|firmware|marlin" | tail -n 80
} >"$DIAG" 2>&1
echo "  diagnostico -> $DIAG"

echo
echo "  IP WiFi: ${IP:-<ninguna>}"
echo "  estado  : $(cat /sys/class/net/${IFACE:-wlan0}/operstate 2>/dev/null || echo n/a)"
ip -brief addr 2>/dev/null | sed 's/^/    /'

sync

if [ -n "$IP" ]; then
    echo
    echo "OK: WiFi conectado con IP $IP"
    echo "    SSH:  ssh ${ADMIN}@${IP}"
    systemctl start k2b-finish.service >/dev/null 2>&1 || true
    echo "    (apt + ufw se completan en segundo plano -> /root/k2b-finish.log)"
elif [ "$NO_REBOOT" = "1" ]; then
    echo
    echo "WiFi aun sin IP y NO_REBOOT=1: no reinicio."
elif [ -e "$BOOT_MARK" ]; then
    echo
    echo "WiFi aun sin IP tras un reinicio. NO reinicio de nuevo (evito bucle)."
    echo "Revisa $DIAG y $LOG."
else
    echo
    echo "WiFi aun sin IP: reinicio UNA vez para aplicar modulos/firmware..."
    touch "$BOOT_MARK"
    sleep 2
    systemctl reboot
fi
