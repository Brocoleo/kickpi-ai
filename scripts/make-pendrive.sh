#!/usr/bin/env bash
# Prepara el pendrive para llevar la imagen a la placa (K2B V2).
#
# Modo FILES (por defecto) — recomendado con monitor+teclado en la placa:
#   copia la imagen DESCOMPRIMIDA + su .sha256 + el instalador a un sistema de
#   ficheros existente del pendrive (FAT32/exFAT). En la placa se monta el
#   pendrive y se ejecuta el instalador, que hace dd + verificación.
#   La placa no necesita xz (la imagen va sin comprimir).
#
# Modo RAW:
#   escribe la imagen cruda en el pendrive (queda como disco arrancable) y
#   verifica leyendo de vuelta. En la placa se clona con un dd directo.
#
# Uso:
#   sudo ./scripts/make-pendrive.sh <imagen.img[.xz]> <dispositivo> \
#        [--mode files|raw] [--format fat32|exfat] [--yes]
#
# --format BORRA y reformatea el pendrive con una única partición del tipo
# indicado (úsalo si el pendrive no tiene sistema de ficheros o no te fías).
set -euo pipefail

IMG=""
DEV=""
MODE="files"
FORMAT=""
ASSUME_YES=0

die(){ echo "error: $*" >&2; exit 1; }
usage(){ sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
	case "$1" in
		-h|--help) usage ;;
		--files|--mode=files)  MODE="files"; shift ;;
		--raw|--mode=raw)      MODE="raw"; shift ;;
		--mode)                MODE="${2:?}"; shift 2 ;;
		--format)              FORMAT="${2:?}"; shift 2 ;;
		--format=*)            FORMAT="${1#--format=}"; shift ;;
		--yes|-y)              ASSUME_YES=1; shift ;;
		-*) die "opción desconocida: $1" ;;
		*) if [[ -z "$IMG" ]]; then IMG="$1"; elif [[ -z "$DEV" ]]; then DEV="$1"; else die "argumento de sobra: $1"; fi; shift ;;
	esac
done

[[ -n "$IMG" && -n "$DEV" ]] || die "uso: $0 <imagen> <dispositivo> [--mode files|raw] [--format fat32|exfat]"
[[ -f "$IMG" ]] || die "no existe la imagen: $IMG"
[[ -b "$DEV" ]] || die "$DEV no es un dispositivo de bloque"
[[ $EUID -eq 0 ]] || die "ejecuta con sudo"
IMG="$(cd "$(dirname "$IMG")" && pwd)/$(basename "$IMG")"
[[ "$MODE" == "files" || "$MODE" == "raw" ]] || die "--mode debe ser files o raw"
[[ -z "$FORMAT" || "$FORMAT" == "fat32" || "$FORMAT" == "exfat" ]] || die "--format debe ser fat32 o exfat"

command -v lsblk >/dev/null || die "falta lsblk (util-linux)"

# ------------------------------------------------------------- comprobaciones
[[ "$(lsblk -ndo TYPE "$DEV")" == "disk" ]] || die "$DEV no es un disco completo (¿pusiste una partición?)"
findmnt -rn -S "$DEV" >/dev/null 2>&1 && die "$DEV contiene el sistema de archivos raíz"
if [[ -n "$(lsblk -nro MOUNTPOINT "$DEV" | tr -d '[:space:]')" && -z "$FORMAT" ]]; then
	die "$DEV (o alguna de sus particiones) está montado; desmóntalo primero"
fi

RM="$(lsblk -ndo RM "$DEV")"; TRAN="$(lsblk -ndo TRAN "$DEV")"
echo "==> Dispositivo : $DEV (extraíble=$RM transporte=${TRAN:-?} modelo=$(lsblk -ndo MODEL "$DEV" | xargs))"
[[ "$RM" == "1" || "$TRAN" == "usb" ]] || die "$DEV no parece extraíble ni USB; me niego a escribir ahí"
echo "==> Tamaño      : $(numfmt --to=iec "$(blockdev --getsize64 "$DEV")")"
echo "==> Modo        : $MODE${FORMAT:+  (formateando como $FORMAT)}"
echo
lsblk -o NAME,SIZE,TYPE,TRAN,RM,FSTYPE,LABEL,MOUNTPOINTS "$DEV"
echo
echo "RIESGO: se borrará el contenido de $DEV."
[[ $ASSUME_YES -eq 1 ]] || { read -r -p "Escribe el nombre del dispositivo ($DEV) para continuar: " OK; [[ "$OK" == "$DEV" ]] || die "abortado"; }

# ------------------------------------------------------- (opcional) reformateo
if [[ -n "$FORMAT" ]]; then
	echo "==> Desmontando y borrando firmas de $DEV"
	for p in $(lsblk -nro NAME "$DEV" | tail -n +2); do umount "/dev/$p" 2>/dev/null || true; done
	wipefs -a "$DEV" >/dev/null
	echo "==> Creando tabla y partición nueva ($FORMAT)"
	if command -v parted >/dev/null 2>&1; then
		parted -s "$DEV" mklabel msdos mkpart primary 1MiB 100%
	else
		printf 'label: dos\n1MiB,100%%,L,*\n' | sfdisk "$DEV" >/dev/null
	fi
	partprobe "$DEV" 2>/dev/null || true
	sleep 1
	PART="$(lsblk -nro NAME "$DEV" | tail -n +2 | head -1)"
	[[ -n "$PART" ]] || die "no apareció la partición nueva en $DEV"
	if [[ "$FORMAT" == "fat32" ]]; then
		command -v mkfs.vfat >/dev/null || die "falta mkfs.vfat (apt install dosfstools)"
		mkfs.vfat -F32 -n K2BIMG "/dev/$PART"
	else
		command -v mkfs.exfat >/dev/null || die "falta mkfs.exfat (apt install exfatprogs)"
		mkfs.exfat -n K2BIMG "/dev/$PART"
	fi
	sleep 1
fi

# ------------------------------------------------------------------ utilidades
uncompressed_bytes(){
	if [[ "$IMG" == *.xz ]]; then
		xz -dc "$IMG" | wc -c
	else
		stat -c%s "$IMG"
	fi
}
plain_name(){ [[ "$IMG" == *.xz ]] && basename "${IMG%.xz}" || basename "$IMG"; }

# =============================================================== MODO FILES
if [[ "$MODE" == "files" ]]; then
	MNT="$(mktemp -d /tmp/k2b-usb.XXXXXX)"
	cleanup(){ umount "$MNT" 2>/dev/null || true; rmdir "$MNT" 2>/dev/null || true; }
	trap cleanup EXIT

	PART="$(lsblk -nro NAME,FSTYPE "$DEV" | awk '$2!="" {print $1; exit}')"
	[[ -n "$PART" ]] || die "el pendrive no tiene ninguna partición con sistema de archivos (usa --format fat32, o --mode raw)"
	FSTYPE="$(lsblk -nro FSTYPE "/dev/$PART" | head -1)"
	case "$FSTYPE" in
		vfat|exfat|ext4|ext3) : ;;
		*) die "sistema de ficheros $FSTYPE no soportado por la placa; usa --format fat32" ;;
	esac
	echo "==> Partición   : /dev/$PART ($FSTYPE)"
	mount "/dev/$PART" "$MNT"

	NEED="$(uncompressed_bytes)"
	FREE="$(df -B1 --output=avail "$MNT" | tail -1)"
	echo "==> Espacio     : libre $(numfmt --to=iec "$FREE"), necesita $(numfmt --to=iec "$NEED")"
	[[ "$FREE" -gt "$((NEED + 20971520))" ]] || die "no cabe en el pendrive (usa --format fat32 o uno más grande)"
	[[ "$NEED" -lt 4294967296 ]] || die "la imagen no cabe en FAT32 (>4GB); usa --format exfat"

	PLAIN="$(plain_name)"
	echo "==> Escribiendo $PLAIN ..."
	if [[ "$IMG" == *.xz ]]; then
		xz -dc "$IMG" > "$MNT/$PLAIN"
	else
		cp -f "$IMG" "$MNT/$PLAIN"
	fi
	sync
	echo "==> Generando y verificando checksum de la copia..."
	( cd "$MNT" && sha256sum "$PLAIN" > "$PLAIN.sha256" && sha256sum -c "$PLAIN.sha256" ) \
		|| die "la copia no coincide con la imagen; NO la uses"

	SRCDIR="$(cd "$(dirname "$0")" && pwd)"
	[[ -f "$SRCDIR/k2b-emmc-install.sh" ]] && install -m 0755 "$SRCDIR/k2b-emmc-install.sh" "$MNT/"
	cat > "$MNT/LEEME.txt" <<EOF
Instalación de la imagen en la eMMC de la KICKPI K2B V2
=======================================================

Pendrive preparado con: $PLAIN
Checksum:               $PLAIN.sha256   (verificado al copiar)

En la placa (con monitor y teclado), desde el sistema que ya arranca:

  1) Conecta este pendrive y averigua su partición:
       lsblk
     (suele ser /dev/sda1)

  2) Móntalo:
       sudo mkdir -p /mnt/usb
       sudo mount /dev/sda1 /mnt/usb

  3) Comprueba el checksum (opcional pero recomendado):
       cd /mnt/usb && sha256sum -c $PLAIN.sha256

  4) Instala en la eMMC (pide confirmación escribiendo SI y verifica al final):
       sudo bash /mnt/usb/k2b-emmc-install.sh /mnt/usb/$PLAIN

  5) Apaga, quita el pendrive Y la tarjeta SD, y enciende:
       sudo poweroff
     Arrancará desde la eMMC y ampliará la raíz automáticamente.

Después: conéctate por WiFi (ssh leandro@<ip>) — la IP aparece en el hotspot.
EOF
	sync
	echo "==> Contenido del pendrive:"
	ls -lh "$MNT" | sed 's/^/    /'
	umount "$MNT"
	echo
	echo "==> OK (files): pendrive listo. Lee LEEME.txt en la placa."
	exit 0
fi

# ================================================================= MODO RAW
PLAIN_SHA="$( { if [[ "$IMG" == *.xz ]]; then xz -dc "$IMG"; else cat "$IMG"; fi; } | sha256sum | cut -d' ' -f1)"
PLAIN_BYTES="$(uncompressed_bytes)"
DEV_BYTES="$(blockdev --getsize64 "$DEV")"
echo "==> Imagen      : $(numfmt --to=iec "$PLAIN_BYTES")  sha256=$PLAIN_SHA"
[[ "$DEV_BYTES" -ge "$PLAIN_BYTES" ]] || die "el pendrive es más pequeño que la imagen"

echo "==> Escribiendo imagen cruda..."
if [[ "$IMG" == *.xz ]]; then
	xz -dc "$IMG" | dd of="$DEV" bs=4M conv=fsync status=progress
else
	dd if="$IMG" of="$DEV" bs=4M conv=fsync status=progress
fi
sync

echo "==> Verificando (leer de vuelta el pendrive)..."
READ_BACK="$({ dd if="$DEV" bs=1M count=$(( (PLAIN_BYTES + 1048575) / 1048576 )) status=none || true; } \
	| head -c "$PLAIN_BYTES" | sha256sum | cut -d' ' -f1)"
echo "    escrito : $PLAIN_SHA"
echo "    leído   : $READ_BACK"
[[ "$READ_BACK" == "$PLAIN_SHA" ]] || die "LA VERIFICACIÓN FALLÓ: el pendrive no contiene la imagen correcta"
sync

echo
echo "==> OK (raw): copia arrancable verificada."
echo "    En la placa, para clonar al eMMC:  sudo dd if=/dev/sda of=/dev/mmcblk2 bs=4M conv=fsync status=progress"
