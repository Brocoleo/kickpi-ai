#!/usr/bin/env bash
# Descarga el firmware del chip WiFi/BT Seekwave SWT6621S (KICKPI K2B V2 REV2)
# desde el repositorio Novice-PG/swt6621s y verifica cada blob con el
# manifiesto SHA256SUMS publicado por ese mismo repositorio.
#
# El nombre de los ficheros coincide con lo que pide el driver del kernel
# (skw_sdio_lite / swt6621s_wifi), por lo que no hay que adivinar alias:
# el instalado final (customize-image.sh) solo añade los 2 alias que el driver
# también consulta.
#
# Uso:
#   ./scripts/fetch-swt6621s-firmware.sh [directorio-destino]
set -euo pipefail

# Pin inmutable (tag 1.0.2 = commit). Actualizar a propósito, nunca "main".
SWT6621S_REPO="Novice-PG/swt6621s"
SWT6621S_PIN="${SWT6621S_PIN:-76f43d7f9d9253554e6c2c99db017f1a8b983205}"
BASE_URL="https://raw.githubusercontent.com/${SWT6621S_REPO}/${SWT6621S_PIN}/firmware"

OUT_DIR="${1:-firmware/swt6621s}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

echo "==> Origen : ${SWT6621S_REPO} @ ${SWT6621S_PIN:0:12}"
echo "==> Destino: $OUT_DIR"

if ! command -v curl >/dev/null 2>&1; then
	echo "error: se requiere curl" >&2
	exit 1
fi

# Manifiesto de checksums publicado por el repo
curl -fsSL -o "$OUT_DIR/SHA256SUMS" "$BASE_URL/SHA256SUMS"

# El manifiesto lista todos los blobs; descargamos exactamente esos.
# (.bin de DRAM/IRAM/NV/calib y .nvbin de Bluetooth)
mapfile -t FILES < <(awk '/\.(nv)?bin$/ {print $NF}' "$OUT_DIR/SHA256SUMS" | sort -u)
if [[ "${#FILES[@]}" -eq 0 ]]; then
	echo "error: SHA256SUMS no contiene entradas .bin/.nvbin" >&2
	exit 1
fi

echo "==> Blobs declarados: ${#FILES[@]}"
for f in "${FILES[@]}"; do
	printf '    - %s\n' "$f"
done

for f in "${FILES[@]}"; do
	echo "==> descargando $f"
	curl -fsSL -o "$OUT_DIR/$f" "$BASE_URL/$f"
done

# Verificación estricta contra el manifiesto
echo "==> Verificando SHA256..."
(
	cd "$OUT_DIR"
	awk '/\.(nv)?bin$/ {print}' SHA256SUMS > .sha256.check
	sha256sum -c --strict .sha256.check
	rm -f .sha256.check
)

echo
echo "==> Firmware listo y verificado en: $OUT_DIR"
ls -l "$OUT_DIR"
