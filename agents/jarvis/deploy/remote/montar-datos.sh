#!/usr/bin/env bash
# =============================================================================
# montar-datos.sh — se ejecuta EN LA PLACA (con sudo) cuando /mnt/data enferma.
#
# Motivo de existir: el 2026-10-22 el disco USB se desenganchó del bus a media
# mañana y quedó montado "atascado" sobre un dispositivo que ya no existía
# (pasó de sda a sdb): TODO daba error de E/S, incluida la recreación de los
# contenedores, y el agente se quedó en bucle de reinicio.
#
# Qué hace, en orden seguro:
#   1. Diagnóstico: ¿existe el dispositivo? ¿montado? ¿responde a escritura?
#   2. Si el montaje está atascado: desmontaje perezoso + remontaje (la vía
#      fstab, que usa UUID y sobrevive a la renumeración del disco).
#   3. Si tras remontar sigue fallando la E/S: detiene la placa y da
#      instrucciones para REVISAR cables y luego FSCK A LA FRÍA, porque con
#      el sistema de ficheros a medias cada intento en caliente lo empeora.
#
# Uso: ssh -t placa 'sudo bash /home/leandro/jarvis/deploy/remote/montar-datos.sh'
# =============================================================================
set -u

MONTAJE=/mnt/data
FSTAB="$(grep -E "^[^#].*\\s+${MONTAJE}\\s+" /etc/fstab | head -1 || true)"

rojo() { printf '\033[31m%s\033[0m\n' "$*"; }
verde() { printf '\033[32m%s\033[0m\n' "$*"; }
ambar() { printf '\033[33m%s\033[0m\n' "$*"; }

echo "== 1/3 diagnóstico"

if [ -z "$FSTAB" ]; then
  rojo "✗ no encuentro la línea de /mnt/data en /etc/fstab; salgo sin tocar nada"
  exit 1
fi
ambar "fstab: $FSTAB"

# Dispositivo de la partición (va de último en la línea: UUID ... /mnt/data ext4 ...)
UUID="$(printf '%s' "$FSTAB" | awk '{print $1}' | sed 's/^UUID=//')"
DISCO="$(ls /dev/disk/by-uuid/ 2>/dev/null | grep -m1 "^${UUID}$" || true)"
# El symlink apunta a ../../sda1: de ahí sacamos el nodo actual.
NODO="$(readlink -f "/dev/disk/by-uuid/$UUID" 2>/dev/null || true)"

if [ -z "$NODO" ] || [ ! -b "$NODO" ]; then
  rojo "✗ el disco NO está conectado (no existe el dispositivo del UUID $UUID)"
  cat <<'AYUDA'

  1) sudo shutdown now
  2) reconecta el disco (datos y alimentación; prueba cable/puerto)
  3) enciende y vuelve a ejecutar este script
  Si reaparece el problema sin que nadie toque el hardware: fallo físico
  del disco o del puente USB → plan de emergencia (restaurar desde copia).
AYUDA
  exit 1
fi
verde "disco presente: $NODO"

if mountpoint -q "$MONTAJE"; then
  echo "montado como: $(findmnt -n -o SOURCE "$MONTAJE")"
else
  ambar "$MONTAJE no está montado"
fi

escritura_ok() {
  # prueba de verdad: crear Y borrar (solo tocar no basta si la cuota/permiso
  # mienten... y el > también falla en ro).
  ( touch "$MONTAJE/.prueba-datos" && rm -f "$MONTAJE/.prueba-datos" ) 2>/dev/null
}

echo "== 2/3 prueba de escritura"
if escritura_ok; then
  verde "✔ la escritura funciona; nada que sanar"
  exit 0
fi
ambar "la escritura falla: saneamiento del montaje atascado"

echo "== 3/3 re-montando"
umount "$MONTAJE" 2>/dev/null || umount -l "$MONTAJE" 2>/dev/null || true
if ! mount "$MONTAJE"; then
  rojo "✗ el kernel no pudo montar el sistema de ficheros (¿superbloque sucio?)"
  cat <<'AYUDA'

  Con el FS a medias, no lo toques más en caliente:
  1) sudo shutdown now
  2) reconecta el disco (datos y alimentación)
  3) enciende y ejecuta: sudo fsck -f /dev/<disco>   (el nodo lo decía arriba)
  4) tras fsck: sudo mount /mnt/data  y  sudo docker compose
     -f /home/leandro/jarvis/docker-compose.yml up -d --force-recreate
AYUDA
  exit 1
fi

if escritura_ok; then
  verde "✔ montado y se escribe de nuevo"
  cat <<'CIERRE'

  Falta darle aire fresco a los contenedores (sus montajes son viejos):
    sudo docker compose -f /home/leandro/jarvis/docker-compose.yml up -d --force-recreate

  Y vigilar: si el disco reaparece sin que nadie lo toque, el problema es
  hardware (cable, puente USB o el disco) → plan de cambio.
CIERRE
else
  rojo "✗ montado pero la E/S sigue fallando"
  echo "  correr: dmesg -T | tail -30 y decidir fsck a la fría (guía arriba)"
  exit 1
fi
