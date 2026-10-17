#!/usr/bin/env bash
# Instala la imagen en la eMMC de la placa. EJECUTAR EN LA PLACA (por consola o
# red), desde el sistema que ya arranca (la SD actual de Armbian).
#
# Uso:
#   sudo ./k2b-emmc-install.sh <origen> [destino] [--dry-run] [--yes]
#
#   origen   fichero .img  |  fichero .img.xz  |  dispositivo (p.ej. /dev/sda)
#            (en el modo raw del pendrive, el origen es el propio dispositivo)
#   destino  por defecto /dev/mmcblk2 (eMMC 29GB de la K2B V2)
#
# Comprobaciones antes de escribir:
#   * el destino no es el dispositivo donde está montada la raíz
#   * el destino no tiene particiones montadas (se desmontan)
#   * el destino es un disco de >= 8 GiB
#   * hay espacio suficiente
#   * hay que escribir "SI" para confirmar
# Después de escribir, VERIFICA leyendo de vuelta el eMMC y comparando SHA256.
set -euo pipefail

SRC=""
TARGET="/dev/mmcblk2"
DRY=0
ASSUME_YES=0

die(){ echo "error: $*" >&2; exit 1; }
usage(){ sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
	case "$1" in
		-h|--help) usage ;;
		--dry-run) DRY=1; shift ;;
		--yes|-y)  ASSUME_YES=1; shift ;;
		-*) die "opción desconocida: $1" ;;
		*) if [[ -z "$SRC" ]]; then SRC="$1"; else TARGET="$1"; fi; shift ;;
	esac
done

[[ -n "$SRC" ]] || die "falta el origen (uso: $0 <origen> [destino])"
[[ $EUID -eq 0 ]] || die "ejecuta con sudo"

# ------------------------------------------------------------------ identidad
ROOT_SRC="$(findmnt -n -o SOURCE / 2>/dev/null || echo '?')"
ROOT_DISK="$(lsblk -nro PKNAME "$ROOT_SRC" 2>/dev/null | head -1 || true)"
[[ -n "$ROOT_DISK" ]] || ROOT_DISK="$(printf '%s' "$ROOT_SRC" | sed 's/p\?[0-9]*$//' | sed 's|/dev/||')"

echo "==> Sistema en ejecución: raíz=$ROOT_SRC  disco=$ROOT_DISK"
echo "==> Origen  : $SRC"
echo "==> Destino : $TARGET"

[[ -e "$SRC" ]] || die "no existe el origen: $SRC"
[[ -b "$TARGET" ]] || die "$TARGET no es un dispositivo de bloque"
[[ "$(lsblk -ndo TYPE "$TARGET")" == "disk" ]] || die "$TARGET no es un disco completo"
[[ "$(basename "$TARGET")" != "$ROOT_DISK" ]] || die "$TARGET es el disco del sistema en ejecución; me niego"

TARGET_BYTES="$(blockdev --getsize64 "$TARGET")"
[[ "$TARGET_BYTES" -ge 8589934592 ]] || die "el destino es demasiado pequeño ($(numfmt --to=iec "$TARGET_BYTES"))"
echo "==> Destino : $(numfmt --to=iec "$TARGET_BYTES")  modelo=$(lsblk -ndo MODEL "$TARGET" | xargs)"

# ------------------------------------------------------------- tamaño/origen
if [[ -b "$SRC" ]]; then
	# pendrive en modo raw: el tamaño real de la imagen es hasta el final de
	# la última partición de su tabla (el resto del pendrive no importa).
	LAST_END="$(partx -g -o END "$SRC" 2>/dev/null | sort -n | tail -1 || true)"
	if [[ -n "$LAST_END" && "$LAST_END" -gt 0 ]]; then
		SRC_BYTES=$(( (LAST_END + 1) * 512 ))
		echo "==> Origen  : disco, imagen ~$(numfmt --to=iec "$SRC_BYTES") (según su tabla de particiones)"
	else
		SRC_BYTES="$(blockdev --getsize64 "$SRC")"
		echo "==> Origen  : disco completo $(numfmt --to=iec "$SRC_BYTES") (sin tabla de particiones legible)"
	fi
	SRC_KIND="dev"
elif [[ "$SRC" == *.xz ]]; then
	command -v xz >/dev/null || die "falta xz (apt install xz-utils) para descomprimir $SRC"
	SRC_BYTES="$(xz -l --robot "$SRC" | awk '/^totals/{print $5}')"
	[[ -n "$SRC_BYTES" && "$SRC_BYTES" -gt 0 ]] || die "no pude leer el tamaño descomprimido de $SRC"
	echo "==> Origen  : fichero .img.xz, $(numfmt --to=iec "$SRC_BYTES") descomprimido"
	SRC_KIND="xz"
else
	SRC_BYTES="$(stat -c%s "$SRC")"
	echo "==> Origen  : fichero $(numfmt --to=iec "$SRC_BYTES")"
	SRC_KIND="file"
fi

[[ "$TARGET_BYTES" -ge "$SRC_BYTES" ]] || die "el destino ($(numfmt --to=iec "$TARGET_BYTES")) es más pequeño que la imagen ($(numfmt --to=iec "$SRC_BYTES"))"

# ---------------------------------------------------------------- confirmación
echo
lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS,MODEL | sed 's/^/    /'
echo
echo "RIESGO: se borrará TODO el contenido de $TARGET."
[[ $DRY -eq 1 ]] && echo "(--dry-run: no se escribirá nada)"
if [[ $DRY -eq 0 ]]; then
	[[ $ASSUME_YES -eq 1 ]] || { read -r -p "Escribe SI para continuar: " OK; [[ "$OK" == "SI" ]] || die "abortado"; }
fi

# --------------------------------------------------------- desmontar destino
mapfile -t PARTS < <(lsblk -nro NAME "$TARGET" | tail -n +2)
for p in "${PARTS[@]}"; do
	mp="$(lsblk -nro MOUNTPOINT "/dev/$p" | tr -d '[:space:]')"
	if [[ -n "$mp" ]]; then
		echo "==> desmontando /dev/$p ($mp)"
		umount "/dev/$p" 2>/dev/null || umount -l "/dev/$p" 2>/dev/null || true
	fi
done
if [[ -n "$(lsblk -nro MOUNTPOINT "$TARGET" | tr -d '[:space:]')" ]]; then
	die "alguna partición de $TARGET sigue montada; desmóntala y reintenta"
fi

# ------------------------------------------------------------------- escritura
if [[ $DRY -eq 1 ]]; then
	echo "==> (dry-run) escribiría $SRC_BYTES bytes en $TARGET"
	exit 0
fi

echo "==> Escribiendo en $TARGET ..."
case "$SRC_KIND" in
	xz)   xz -dc "$SRC" | dd of="$TARGET" bs=4M conv=fsync status=progress ;;
	dev|file) dd if="$SRC" of="$TARGET" bs=4M count=$(( (SRC_BYTES + 4194303) / 4194304 )) conv=fsync status=progress ;;
esac
sync

# Tras escribir aparece la tabla de particiones nueva del eMMC; si el sistema
# monta alguna de esas particiones, los bytes cambiarían durante la
# verificación. Desmontamos cualquier partición del destino antes de leerla.
mapfile -t PARTS_NEW < <(lsblk -nro NAME "$TARGET" | tail -n +2)
for p in "${PARTS_NEW[@]}"; do
	umount "/dev/$p" 2>/dev/null || umount -l "/dev/$p" 2>/dev/null || true
done

# --------------------------------------------------------------- verificación
echo "==> Verificando (leer de vuelta el eMMC, puede tardar un poco)..."
case "$SRC_KIND" in
	xz)   SRC_SHA="$(xz -dc "$SRC" | head -c "$SRC_BYTES" | sha256sum | cut -d' ' -f1)" ;;
	*)    SRC_SHA="$({ dd if="$SRC" bs=1M count=$(( (SRC_BYTES + 1048575) / 1048576 )) status=none || true; } | head -c "$SRC_BYTES" | sha256sum | cut -d' ' -f1)" ;;
esac
DST_SHA="$({ dd if="$TARGET" bs=1M count=$(( (SRC_BYTES + 1048575) / 1048576 )) status=none || true; } | head -c "$SRC_BYTES" | sha256sum | cut -d' ' -f1)"
echo "    origen : $SRC_SHA"
echo "    eMMC   : $DST_SHA"
[[ "$SRC_SHA" == "$DST_SHA" ]] || die "LA VERIFICACIÓN FALLÓ: no reinicies, reintenta la escritura"
sync

cat <<EOF

==> OK: imagen escrita y VERIFICADA en $TARGET

Siguiente paso (importante):
  1) apaga:            sudo poweroff
  2) quita el pendrive
  3) quita la SD (tiene prioridad de arranque sobre la eMMC)
  4) enciende: arrancará desde la eMMC y ampliará la raíz automáticamente
  5) conéctate:        ssh leandro@<ip>   (la IP la verás en el hotspot)
EOF
