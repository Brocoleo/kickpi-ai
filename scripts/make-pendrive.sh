#!/usr/bin/env bash
# Prepara el pendrive para llevar la imagen a la placa.
#
# Modos:
#   raw   (por defecto) escribe la IMAGEN CRUDA en el pendrive, que queda como
#         disco de arranque. En la placa se clona al eMMC con un dd directo
#         (no hace falta descomprimir ni montar nada allí).
#   files copia el .img.xz + .sha256 + k2b-emmc-install.sh a un sistema de
#         ficheros ya existente del pendrive (FAT32/exFAT). La placa
#         descomprime al instalar.
#
# Uso:
#   sudo ./scripts/make-pendrive.sh <imagen.img.xz|imagen.img> <dispositivo> [--files] [--yes]
#
# Seguridades: exige que el dispositivo sea extraíble/USB, no esté montado, no
# contenga la raíz del sistema, tenga tamaño suficiente, y pide escribir el
# nombre del dispositivo para confirmar. Verifica leyendo de vuelta (modo raw).
set -euo pipefail

IMG=""
DEV=""
MODE="raw"
ASSUME_YES=0

die(){ echo "error: $*" >&2; exit 1; }
usage(){ sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
	case "$1" in
		-h|--help) usage ;;
		--raw)     MODE="raw"; shift ;;
		--files)   MODE="files"; shift ;;
		--yes|-y)  ASSUME_YES=1; shift ;;
		-*) die "opción desconocida: $1" ;;
		*) if [[ -z "$IMG" ]]; then IMG="$1"; elif [[ -z "$DEV" ]]; then DEV="$1"; else die "argumento de sobra: $1"; fi; shift ;;
	esac
done

[[ -n "$IMG" && -n "$DEV" ]] || die "uso: $0 <imagen> <dispositivo> [--files]"
[[ -f "$IMG" ]] || die "no existe la imagen: $IMG"
[[ -b "$DEV" ]] || die "$DEV no es un dispositivo de bloque"
[[ $EUID -eq 0 ]] || die "ejecuta con sudo"
IMG="$(cd "$(dirname "$IMG")" && pwd)/$(basename "$IMG")"

command -v lsblk >/dev/null || die "falta lsblk (paquete util-linux)"

# ------------------------------------------------------------- comprobaciones
[[ "$(lsblk -ndo TYPE "$DEV")" == "disk" ]] || die "$DEV no es un disco completo (¿pusiste una partición?)"

if findmnt -rn -S "$DEV" >/dev/null 2>&1; then die "$DEV contiene el sistema de archivos raíz"; fi
if [[ -n "$(lsblk -nro MOUNTPOINT "$DEV" | tr -d '[:space:]')" ]]; then
	die "$DEV (o alguna de sus particiones) está montado; desmóntalo primero"
fi

RM="$(lsblk -ndo RM "$DEV")"; TRAN="$(lsblk -ndo TRAN "$DEV")"
echo "==> Dispositivo : $DEV  (extraíble=$RM transporte=${TRAN:-?})"
if [[ "$RM" != "1" && "$TRAN" != "usb" ]]; then
	die "$DEV no parece extraíble ni USB; me niego a escribir ahí"
fi

DEV_BYTES="$(blockdev --getsize64 "$DEV")"
DEV_HUM="$(numfmt --to=iec "$DEV_BYTES" 2>/dev/null || echo "$DEV_BYTES")"
echo "==> Tamaño      : $DEV_HUM"
echo "==> Modelo      : $(lsblk -ndo MODEL "$DEV" 2>/dev/null | xargs)"
echo

lsblk -o NAME,SIZE,TYPE,TRAN,RM,MODEL,MOUNTPOINTS "$DEV"
echo

if [[ "$MODE" == "raw" ]]; then
	echo "Modo RAW: el pendrive se convertirá en una copia arrancable de la imagen."
	echo "Se borrará TODO su contenido."
else
	echo "Modo FILES: se copiarán los ficheros a un sistema de archivos existente."
fi
[[ $ASSUME_YES -eq 1 ]] || { read -r -p "Escribe el nombre del dispositivo ($DEV) para continuar: " OK; [[ "$OK" == "$DEV" ]] || die "abortado"; }

# ---------------------------------------------------------------- modo files
if [[ "$MODE" == "files" ]]; then
	MNT="$(mktemp -d /tmp/k2b-usb.XXXXXX)"
	trap 'umount "$MNT" 2>/dev/null || true; rmdir "$MNT" 2>/dev/null || true' EXIT
	PART="$(lsblk -nro NAME,FSTYPE "$DEV" | awk '$2!="" {print $1; exit}')"
	[[ -n "$PART" ]] || die "el pendrive no tiene ninguna partición con sistema de archivos (usa el modo raw)"
	mount "/dev/$PART" "$MNT"
	NEED="$(stat -c%s "$IMG")"
	FREE="$(df -B1 --output=avail "$MNT" | tail -1)"
	[[ "$FREE" -gt "$((NEED + 1048576))" ]] || die "no hay espacio en el pendrive (necesita $(numfmt --to=iec "$NEED"), libre $(numfmt --to=iec "$FREE"))"
	echo "==> Copiando $(basename "$IMG") ..."
	cp -f "$IMG" "$MNT/"
	sync
	echo "==> Generando y verificando el checksum de la copia..."
	( cd "$MNT" && sha256sum "$(basename "$IMG")" > "$(basename "$IMG").sha256" \
		&& sha256sum -c "$(basename "$IMG").sha256" ) || die "la copia no coincide"
	# script de instalación en la eMMC, para poder ejecutarlo desde la placa
	SRCDIR="$(cd "$(dirname "$0")" && pwd)"
	if [[ -f "$SRCDIR/k2b-emmc-install.sh" ]]; then
		install -m 0755 "$SRCDIR/k2b-emmc-install.sh" "$MNT/"
	fi
	sync
	umount "$MNT"
	echo "==> OK (files): contenido copiado y verificado en $DEV"
	exit 0
fi

# ----------------------------------------------------------------- modo raw
if [[ "$IMG" == *.xz ]]; then
	echo "==> Calculando checksum y tamaño de la imagen descomprimida..."
	PLAIN_SHA="$(xz -dc "$IMG" | sha256sum | cut -d' ' -f1)"
	PLAIN_BYTES="$(xz -dc "$IMG" | wc -c)"
else
	PLAIN_SHA="$(sha256sum "$IMG" | cut -d' ' -f1)"
	PLAIN_BYTES="$(stat -c%s "$IMG")"
fi
echo "    bytes=$PLAIN_BYTES  sha256=$PLAIN_SHA"
[[ "$DEV_BYTES" -ge "$PLAIN_BYTES" ]] || die "el pendrive es más pequeño que la imagen ($DEV_HUM < $(numfmt --to=iec "$PLAIN_BYTES"))"

echo "==> Escribiendo imagen..."
if [[ "$IMG" == *.xz ]]; then
	xz -dc "$IMG" | dd of="$DEV" bs=4M conv=fsync status=progress
else
	dd if="$IMG" of="$DEV" bs=4M conv=fsync status=progress
fi
sync

echo "==> Verificando lectura (leer de vuelta del pendrive)..."
READ_BACK="$( { dd if="$DEV" bs=1M count=$(( (PLAIN_BYTES + 1048575) / 1048576 )) status=none || true; } \
	| head -c "$PLAIN_BYTES" | sha256sum | cut -d' ' -f1)"
echo "    escrito : $PLAIN_SHA"
echo "    leído   : $READ_BACK"
[[ "$READ_BACK" == "$PLAIN_SHA" ]] || die "LA VERIFICACIÓN FALLÓ: el pendrive no contiene la imagen correcta"
sync

echo
echo "==> OK (raw): el pendrive es una copia arrancable y verificada de la imagen."
echo "    Ahora, en la placa:  sudo dd if=$DEV of=/dev/mmcblk2 bs=4M conv=fsync status=progress"
echo "    (o usa scripts/k2b-emmc-install.sh $DEV que hace lo mismo con comprobaciones)"
