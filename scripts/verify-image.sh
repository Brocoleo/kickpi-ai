#!/usr/bin/env bash
# Verifica una imagen ya personalizada ANTES de grabarla/instalarla.
# Monta la partición raíz en solo-lectura y comprueba lo que tiene que estar:
# firmware del SWT6621S, módulos del kernel, red, usuario, arranque, servicios.
#
# Uso:
#   sudo ./scripts/verify-image.sh <imagen.img> [--login leandro] [--hostname kickpi-k2b-v2]
#
# Sale con código 1 si algo CRÍTICO falla (y 0 si todo lo crítico está bien,
# aunque haya avisos).
set -euo pipefail

IMG=""
LOGIN_USER="leandro"
TARGET_HOSTNAME="kickpi-k2b-v2"

die(){ echo "error: $*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
	case "$1" in
		-h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
		--login)    LOGIN_USER="${2:?}"; shift 2 ;;
		--hostname) TARGET_HOSTNAME="${2:?}"; shift 2 ;;
		-*) die "opción desconocida: $1" ;;
		*)  IMG="$1"; shift ;;
	esac
done
[[ -n "$IMG" ]] || die "uso: $0 <imagen.img>"
[[ -f "$IMG" ]] || die "no existe la imagen: $IMG"
[[ $EUID -eq 0 ]] || die "ejecuta con sudo (necesita montar la imagen)"
IMG="$(cd "$(dirname "$IMG")" && pwd)/$(basename "$IMG")"

MNT="$(mktemp -d /tmp/k2b-ver.XXXXXX)"
LOOP=""
cleanup(){ set +e; mountpoint -q "$MNT" && umount "$MNT"; [[ -n "$LOOP" ]] && losetup -d "$LOOP"; rmdir "$MNT" 2>/dev/null || true; }
trap cleanup EXIT

LOOP="$(losetup --partscan --find --show "$IMG")"
for _ in $(seq 1 40); do [[ -b "${LOOP}p1" ]] && break; sleep 0.25; done
[[ -b "${LOOP}p1" ]] || die "no encontré la partición raíz en $IMG"
mount -o ro "${LOOP}p1" "$MNT"

OK=0; BAD=0; WARN=0
chk(){ # <crit|warn> <descripción> <comando...>
	local lvl="$1" desc="$2"; shift 2
	if "$@" >/dev/null 2>&1; then
		printf '  [ OK ]  %s\n' "$desc"; OK=$((OK+1))
	else
		if [[ "$lvl" == crit ]]; then printf '  [FALLA] %s\n' "$desc"; BAD=$((BAD+1))
		else printf '  [aviso] %s\n' "$desc"; WARN=$((WARN+1)); fi
	fi
}
size_of(){ stat -c%s "$1" 2>/dev/null || echo 0; }
FW="$MNT/lib/firmware"

echo "==> Imagen: $IMG"
echo "==> Tamaño: $(numfmt --to=iec "$(stat -c%s "$IMG")")"
echo
echo "--- Firmware SWT6621S en /lib/firmware ---"
chk crit "SWT6621S_DRAM_SDIO.bin (>100 KB)"        test "$(size_of "$FW/SWT6621S_DRAM_SDIO.bin")" -gt 102400
chk crit "SWT6621S_IRAM_SDIO.bin (>100 KB)"        test "$(size_of "$FW/SWT6621S_IRAM_SDIO.bin")" -gt 102400
chk crit "SWT6621S_NV_SDIO.bin es blob real (>100 B, no el stub de 26 B)" test "$(size_of "$FW/SWT6621S_NV_SDIO.bin")" -gt 100
chk crit "SEEKWAVE_NV_SWT6621S.bin es blob real (>1000 B, no el stub de 28 B)" test "$(size_of "$FW/SEEKWAVE_NV_SWT6621S.bin")" -gt 1000
for r in 00000 00001 04000 04001; do
	chk warn "SWT6621S_SEEKWAVE_R${r}.bin presente" test "$(size_of "$FW/SWT6621S_SEEKWAVE_R${r}.bin")" -gt 1000
done
chk crit "sv6160lite.nvbin (Bluetooth) presente"   test -s "$FW/sv6160lite.nvbin"

echo "--- Módulos del kernel en la imagen ---"
MODDIR="$(ls -d "$MNT"/lib/modules/* 2>/dev/null | head -1 || true)"
if [[ -n "$MODDIR" ]]; then
	echo "         versión: $(basename "$MODDIR")"
	chk crit "swt6621s_wifi.ko"  test -n "$(find "$MODDIR" -name 'swt6621s_wifi.ko*' -print -quit)"
	chk crit "skw_sdio_lite.ko"  test -n "$(find "$MODDIR" -name 'skw_sdio_lite.ko*' -print -quit)"
	chk crit "skwbt.ko (Bluetooth)" test -n "$(find "$MODDIR" -name 'skwbt.ko*' -print -quit)"
	KREL="$(basename "$MODDIR")"
	case "$KREL" in
		6.12.*) echo "  [ OK ]  kernel 6.12.x (el driver del vendedor compila en esta serie)" ;;
		*) printf '  [aviso] kernel %s (se esperaba 6.12.x)\n' "$KREL" ;;
	esac
else
	chk crit "directorio /lib/modules con módulos" false
fi

echo "--- Módulos que se cargan al arrancar ---"
chk crit "modules-load.d incluye skw_sdio_lite y swt6621s_wifi" \
	grep -q 'skw_sdio_lite' "$MNT"/etc/modules-load.d/*.conf
chk crit "modules-load.d incluye skwbt" \
	grep -q 'skwbt' "$MNT"/etc/modules-load.d/*.conf

echo "--- Red / WiFi ---"
chk crit "/boot/credentials.txt (WiFi del primer arranque)" test -s "$MNT/boot/credentials.txt"
chk crit "credentials.txt con ENABLE=\"true\"" grep -q '^ENABLE="true"' "$MNT/boot/credentials.txt"
chk crit "credentials.txt con SSID y PASSKEY" bash -c "grep -q '^SSID=' '$MNT/boot/credentials.txt' && grep -q '^PASSKEY=' '$MNT/boot/credentials.txt'"
chk warn "MAC de wlan0 estable (cloned-mac-address)" grep -q 'wifi.cloned-mac-address=stable' "$MNT/etc/NetworkManager/conf.d/20-k2b-cloned-mac.conf"
chk crit "NetworkManager instalado" test -x "$MNT/usr/sbin/NetworkManager" -o -x "$MNT/usr/bin/nmcli" -o -s "$MNT/usr/sbin/NetworkManager"

echo "--- Usuario, acceso y sudo ---"
chk crit "usuario '$LOGIN_USER' existe" grep -q "^${LOGIN_USER}:" "$MNT/etc/passwd"
chk crit "'$LOGIN_USER' está en el grupo sudo" awk -F: -v u="$LOGIN_USER" '$1=="sudo"{n=split($4,a,","); for(i=1;i<=n;i++) if(a[i]==u) f=1} END{exit(f?0:1)}' "$MNT/etc/group"
chk crit "sudo PIDE contraseña (sin NOPASSWD)" bash -c "! ls '$MNT'/etc/sudoers.d/010_${LOGIN_USER}-nopasswd >/dev/null 2>&1"
chk crit "sshd instalado" test -x "$MNT/usr/sbin/sshd"
chk warn "ssh habilitado en el arranque" bash -c "ls '$MNT'/etc/systemd/system/*.wants/ssh.service >/dev/null 2>&1 || ls '$MNT'/etc/rc*.d/S*ssh >/dev/null 2>&1"

echo "--- Sistema ---"
chk crit "hostname == $TARGET_HOSTNAME" bash -c "test \"\$(cat '$MNT/etc/hostname')\" = '$TARGET_HOSTNAME'"
chk crit "timezone America/Santiago" bash -c "test \"\$(cat '$MNT/etc/timezone' 2>/dev/null)\" = 'America/Santiago'"
chk crit "zona horaria real (/etc/localtime)" test -L "$MNT/etc/localtime" -o -f "$MNT/etc/localtime"

echo "--- Arranque ---"
chk crit "extlinux.conf presente" test -s "$MNT/boot/extlinux/extlinux.conf"
chk crit "extlinux apunta al DTB de la K2B rev2" grep -q 'sun50i-h618-kickpi-k2b-rev-2' "$MNT/boot/extlinux/extlinux.conf"
chk crit "extlinux define root=" grep -q 'root=' "$MNT/boot/extlinux/extlinux.conf"
chk warn "extlinux.conf con net.ifnames=0" grep -q 'net.ifnames=0' "$MNT/boot/extlinux/extlinux.conf"
# Referencias kernel/initrd/fdt resueltas como lo hace extlinux (relativas a
# /boot/extlinux, por lo que "../X" = /boot/X). Si alguna no existe, la placa
# no arranca.
EXT="$MNT/boot/extlinux/extlinux.conf"
if [[ -f "$EXT" ]]; then
	while IFS=$'\t' read -r key path; do
		[[ -n "$path" ]] || continue
		case "$path" in
			/*)   full="$MNT$path" ;;
			../*) full="$MNT/boot/${path#../}" ;;
			*)    full="$MNT/boot/extlinux/$path" ;;
		esac
		chk crit "extlinux: $key $path (-> ${full#"$MNT"})" test -f "$full"
	done < <(grep -E '^[[:space:]]*(kernel|initrd|fdt)[[:space:]]+' "$EXT" | awk '{print $1"\t"$2}')
fi
chk warn "servicio firstboot (amplía la raíz al primer arranque)" test -f "$MNT/etc/systemd/system/firstboot.service"
chk warn "servicio credentials (aplica el WiFi)" test -f "$MNT/etc/systemd/system/credentials.service"

# U-Boot/ATF van escritos crudos al principio de la imagen (offset 8 KiB), fuera
# de la partición: si esa zona está a ceros, la placa no arrancaría.
SPL_NONZERO="$(dd if="$IMG" bs=1 skip=8192 count=65536 status=none | tr -d '\0' | wc -c)"
chk crit "U-Boot+ATF en el offset 8 KiB (bytes no nulos=$SPL_NONZERO)" test "$SPL_NONZERO" -gt 100

echo
echo "==> Resumen: $OK correctos, $WARN avisos, $BAD críticos"
if [[ "$BAD" -gt 0 ]]; then
	echo "==> LA IMAGEN NO ESTÁ LISTA: arregla los críticos antes de grabarla." >&2
	exit 1
fi
echo "==> La imagen pasa las comprobaciones críticas."
[[ "$WARN" -gt 0 ]] && echo "    (revisa los avisos: pueden ser cosméticos o de configuración)"
exit 0
