#!/usr/bin/env bash
# Flasheo SD (Fase 4). NO ejecutar sin confirmación explícita.
# Verifica /dev/sdb antes de escribir.
set -euo pipefail
IMG="dist/image.img.xz"
DEV="/dev/sdb"
echo "=== PRE-FLASHEO ==="
lsblk -o NAME,SIZE,MODEL,TRAN,FSTYPE,LABEL,MOUNTPOINTS
echo
echo "Objetivo: $DEV <- $IMG"
echo "RIESGO: se borra todo el contenido de $DEV."
read -r -p "Escribe SI para continuar: " OK
[ "$OK" = "SI" ] || { echo "Abortado."; exit 1; }
[ -f "$IMG" ] || { echo "Falta $IMG. Ejecuta download-verify.sh primero."; exit 1; }
echo "Desmontando ${DEV}*..."
sudo umount ${DEV}* 2>/dev/null || true
echo "Escribiendo (xzcat -> dd, bs=4M, sync)..."
xzcat "$IMG" | sudo dd of="$DEV" bs=4M status=progress conv=fsync
sync
echo "OK. Expulsa con: sudo eject $DEV"
