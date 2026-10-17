#!/usr/bin/env bash
# Personaliza una imagen Debian ya construida por pyavitz/debian-image-builder
# para la KICKPI K2B V2 (Allwinner H618 + WiFi/BT Seekwave SWT6621S).
#
# Qué hace (montando la partición raíz de la imagen por loop, offline):
#   1. Arregla el firmware del SWT6621S en /lib/firmware. El builder YA instala
#      los blobs del vendedor (pyavitz/armbian-firmware), pero deja dos ficheros
#      que el driver pide con nombre propio como "stubs" de texto de 26/28 bytes
#      (un enlace simbólico aplanado por git):
#         SWT6621S_NV_SDIO.bin        -> "SWT6621S_NV_SDIO_SHARE.bin"
#         SEEKWAVE_NV_SWT6621S.bin    -> "SWT6621S_SEEKWAVE_R00000.bin"
#      El driver los carga como firmware NV y falla (-EINVAL), con lo que el
#      wlan0 nunca aparece. Aquí se sustituyen por copias reales del blob
#      (receta verificada en la misma revisión de placa, V2.2).
#      También copia sv6160lite.nvbin (Bluetooth) desde seekwave/*.kickpi,k3b.nvbin.
#      NO se tocan DRAM/IRAM salvo --replace-dram-iram.
#   2. Deja /boot/credentials.txt para que la placa se una al WiFi en el
#      primer arranque (mecanismo oficial del builder, vía NetworkManager).
#   3. (Opcional) MAC de wlan0 estable entre reinicios: el firmware no trae MAC
#      y el driver genera una aleatoria en cada arranque.
#   4. Deja "sudo" pidiendo contraseña (el builder lo deja con NOPASSWD).
#
# Requiere sudo para losetup/mount. Es idempotente.
#
# Uso:
#   sudo ./scripts/customize-image.sh <imagen.img> \
#        --ssid "iPhone de Leandro" --psk "Danae2100" --country CL \
#        --hostname kickpi-k2b-v2 --login leandro \
#        [--firmware-dir firmware/swt6621s]   # respaldo verificado si faltara algo
#        [--replace-dram-iram]                # forzar blobs del respaldo
#        [--no-wifi] [--no-stable-mac]
set -euo pipefail

IMG=""
FIRMWARE_DIR=""
SSID=""
PSK=""
COUNTRY="CL"
TARGET_HOSTNAME=""
LOGIN_USER=""
ENABLE_WIFI=1
STABLE_MAC=1
REPLACE_RAM=0

die(){ echo "error: $*" >&2; exit 1; }
usage(){ sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
	case "$1" in
		-h|--help) usage ;;
		--firmware-dir)   FIRMWARE_DIR="${2:?}"; shift 2 ;;
		--ssid)           SSID="${2:?}"; shift 2 ;;
		--psk)            PSK="${2:?}"; shift 2 ;;
		--country)        COUNTRY="${2:?}"; shift 2 ;;
		--hostname)       TARGET_HOSTNAME="${2:?}"; shift 2 ;;
		--login)          LOGIN_USER="${2:?}"; shift 2 ;;
		--no-wifi)        ENABLE_WIFI=0; shift ;;
		--no-stable-mac)  STABLE_MAC=0; shift ;;
		--replace-dram-iram) REPLACE_RAM=1; shift ;;
		-*)               die "opción desconocida: $1" ;;
		*)                IMG="$1"; shift ;;
	esac
done

[[ -n "$IMG" ]] || die "falta la ruta de la imagen .img"
[[ -f "$IMG" ]] || die "no existe la imagen: $IMG"
[[ $EUID -eq 0 ]] || die "ejecuta con sudo (necesario para losetup/mount)"
IMG="$(cd "$(dirname "$IMG")" && pwd)/$(basename "$IMG")"

if [[ -n "$FIRMWARE_DIR" ]]; then
	[[ -d "$FIRMWARE_DIR" ]] || die "no existe --firmware-dir $FIRMWARE_DIR"
	FIRMWARE_DIR="$(cd "$FIRMWARE_DIR" && pwd)"
fi
if [[ $ENABLE_WIFI -eq 1 ]]; then
	[[ -n "$SSID" ]] || die "falta --ssid"
	[[ -n "$PSK"  ]] || die "falta --psk"
fi

echo "==> Imagen       : $IMG"
echo "==> WiFi         : $([[ $ENABLE_WIFI -eq 1 ]] && echo "SSID='$SSID' country=$COUNTRY" || echo '(omitido)')"
echo "==> MAC estable  : $STABLE_MAC"
echo "==> Respaldo fw  : ${FIRMWARE_DIR:-(ninguno, solo lo que trae la imagen)}"

# --------------------------------------------------------------- loop + mount
MNT="$(mktemp -d /tmp/k2b-mnt.XXXXXX)"
LOOP=""
cleanup(){
	set +e
	if mountpoint -q "$MNT"; then sync; umount "$MNT"; fi
	[[ -n "$LOOP" ]] && losetup -d "$LOOP"
	rmdir "$MNT" 2>/dev/null || true
}
trap cleanup EXIT

LOOP="$(losetup --partscan --find --show "$IMG")"
echo "==> loop device  : $LOOP"
ROOTPART=""
for _ in $(seq 1 40); do [[ -b "${LOOP}p1" ]] && { ROOTPART="${LOOP}p1"; break; }; sleep 0.25; done
if [[ -z "$ROOTPART" ]]; then
	partx -a "$LOOP" >/dev/null 2>&1 || true
	for _ in $(seq 1 40); do [[ -b "${LOOP}p1" ]] && { ROOTPART="${LOOP}p1"; break; }; sleep 0.25; done
fi
[[ -n "$ROOTPART" ]] || die "no se pudo localizar la partición raíz en $IMG"
echo "==> partición    : $ROOTPART"

mount "$ROOTPART" "$MNT"
FW="$MNT/lib/firmware"
[[ -d "$FW" ]] || die "$ROOTPART no parece una raíz Debian ($FW ausente)"

# --------------------------------------------------------------- firmware fix
# Un "stub" es un enlace simbólico aplanado por git: un fichero diminuto cuyo
# contenido es el nombre de otro fichero del mismo directorio.
is_stub(){
	local f="$1" sz content
	[[ -f "$f" ]] || return 1
	sz="$(stat -c%s "$f")"
	[[ "$sz" -le 128 ]] || return 1
	content="$(tr -d '\r\n' < "$f")"
	[[ -n "$content" && "$content" != */* ]] || return 1
	[[ -f "$(dirname "$f")/$content" ]]
}
sha(){ sha256sum "$1" | cut -d' ' -f1; }

# Asegura $name con contenido real, usando $preferred (en la propia imagen)
# o el respaldo verificado como fuente.
ensure_blob(){
	local name="$1" preferred="$2" src="" origin=""
	if [[ -z "$preferred" ]]; then
		# buscar el nombre directamente en la imagen
		[[ -s "$FW/$name" ]] && ! is_stub "$FW/$name" && { echo "      [ok]     $name (ya presente)"; return 0; }
	elif [[ -s "$FW/$preferred" ]] && ! is_stub "$FW/$preferred"; then
		src="$FW/$preferred"; origin="imagen:$preferred"
	fi
	if [[ -z "$src" && -n "$FIRMWARE_DIR" && -s "$FIRMWARE_DIR/$name" ]]; then
		src="$FIRMWARE_DIR/$name"; origin="respaldo:$name"
	fi

	if [[ -z "$src" ]]; then
		echo "      [FALTA]  $name (no está en la imagen ni en el respaldo)"
		return 1
	fi
	if [[ -s "$FW/$name" ]] && ! is_stub "$FW/$name"; then
		if [[ "$(sha "$FW/$name")" == "$(sha "$src")" ]]; then
			echo "      [ok]     $name"
		else
			echo "      [ok]     $name (blob propio distinto del respaldo; se respeta)"
		fi
		return 0
	fi
	local why="creado"
	[[ -e "$FW/$name" ]] && why="reemplazado (era un stub en texto)"
	install -o root -g root -m 0644 "$src" "$FW/$name"
	echo "      [$why] $name  <- $origin"
	return 0
}

echo "==> Firmware SWT6621S en /lib/firmware"
rc=0
ensure_blob SWT6621S_DRAM_SDIO.bin       "" || rc=1
ensure_blob SWT6621S_IRAM_SDIO.bin       "" || rc=1
# NV: el driver pide "SWT6621S_NV_SDIO.bin"; el vendedor distribuye la variante
# SHARE (difieren en 1 byte, offset 0x20: 01 vs 00) -> usar SHARE.
ensure_blob SWT6621S_NV_SDIO.bin         SWT6621S_NV_SDIO_SHARE.bin || rc=1
# Calibración real: el nombre se calcula como <chip>_<proyecto>_R%02X%03X.bin
# (bus + revisión de efuse), así que se dejan las 4 variantes conocidas.
for n in SWT6621S_SEEKWAVE_R00000.bin SWT6621S_SEEKWAVE_R00001.bin \
         SWT6621S_SEEKWAVE_R04000.bin SWT6621S_SEEKWAVE_R04001.bin; do
	ensure_blob "$n" "" || rc=1
done
# Fallback NV por nombre genérico
ensure_blob SEEKWAVE_NV_SWT6621S.bin     SWT6621S_SEEKWAVE_R00000.bin || rc=1
# Bluetooth
if [[ -s "$FW/sv6160lite.nvbin" ]] && ! is_stub "$FW/sv6160lite.nvbin"; then
	echo "      [ok]     sv6160lite.nvbin"
elif [[ -s "$FW/seekwave/sv6160lite.kickpi,k3b.nvbin" ]]; then
	install -o root -g root -m 0644 "$FW/seekwave/sv6160lite.kickpi,k3b.nvbin" "$FW/sv6160lite.nvbin"
	echo "      [creado] sv6160lite.nvbin  <- imagen:seekwave/sv6160lite.kickpi,k3b.nvbin"
elif [[ -n "$FIRMWARE_DIR" && -s "$FIRMWARE_DIR/sv6160lite.nvbin" ]]; then
	install -o root -g root -m 0644 "$FIRMWARE_DIR/sv6160lite.nvbin" "$FW/sv6160lite.nvbin"
	echo "      [creado] sv6160lite.nvbin  <- respaldo"
else
	echo "      [FALTA]  sv6160lite.nvbin (solo afecta a Bluetooth)"
fi

if [[ $REPLACE_RAM -eq 1 && -n "$FIRMWARE_DIR" ]]; then
	echo "==> Reemplazando DRAM/IRAM por los del respaldo (--replace-dram-iram)"
	for n in SWT6621S_DRAM_SDIO.bin SWT6621S_IRAM_SDIO.bin; do
		[[ -s "$FIRMWARE_DIR/$n" ]] || die "respaldo sin $n"
		install -o root -g root -m 0644 "$FIRMWARE_DIR/$n" "$FW/$n"
		echo "      [reemplazado] $n"
	done
fi

[[ $rc -eq 0 ]] || die "firmware incompleto: el wlan0 no aparecería. Revisa el origen del firmware."

# ----------------------------------------------------- WiFi en primer arranque
if [[ $ENABLE_WIFI -eq 1 ]]; then
	echo "==> Escribiendo /boot/credentials.txt"
	install -d -o root -g root -m 0755 "$MNT/boot"
	cat > "$MNT/boot/credentials.txt" <<EOF
ENABLE="true"
SSID="${SSID}"
PASSKEY="${PSK}"
COUNTRYCODE="${COUNTRY}"
MANUAL="false"
EOF
	[[ -n "$TARGET_HOSTNAME" ]] && echo "HOSTNAME=\"${TARGET_HOSTNAME}\"" >> "$MNT/boot/credentials.txt"
	chown root:root "$MNT/boot/credentials.txt"
	chmod 0600 "$MNT/boot/credentials.txt"
fi

# ------------------------------------------------------------- MAC estable
if [[ $STABLE_MAC -eq 1 ]]; then
	echo "==> MAC estable (NetworkManager)"
	install -d -o root -g root -m 0755 "$MNT/etc/NetworkManager/conf.d"
	cat > "$MNT/etc/NetworkManager/conf.d/20-k2b-cloned-mac.conf" <<'EOF'
[connection]
# La MAC del SWT6621S es aleatoria (el firmware no trae MAC). Derivar una MAC
# estable evita que la IP de la placa cambie en cada reinicio.
wifi.cloned-mac-address=stable
EOF
	chown root:root "$MNT/etc/NetworkManager/conf.d/20-k2b-cloned-mac.conf"
	chmod 0644 "$MNT/etc/NetworkManager/conf.d/20-k2b-cloned-mac.conf"
fi

# ------------------------------------------------------- sudo con contraseña
if [[ -n "$LOGIN_USER" ]]; then
	rm -f "$MNT/etc/sudoers.d/010_${LOGIN_USER}-nopasswd"
	echo "==> sudo pedirá contraseña para '${LOGIN_USER}'"
fi

# ------------------------------------------------------------------- reporte
echo
echo "==> Firmware final en /lib/firmware"
ls -l "$FW" | grep -E 'SWT6621S|SEEKWAVE|sv6160' | awk '{printf "      %10s  %s\n",$5,$9}' || true
echo
echo "==> Modo de arranque del rootfs"
grep -E "^root=" "$MNT/boot/extlinux/extlinux.conf" 2>/dev/null | sed 's/^/      /' || echo "      (sin extlinux.conf)"

sync
echo
echo "==> OK: imagen personalizada y lista para grabar: $IMG"
