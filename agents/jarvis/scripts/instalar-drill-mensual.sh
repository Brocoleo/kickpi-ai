#!/usr/bin/env bash
# =============================================================================
# instalar-drill-mensual.sh — instala (o actualiza) el simulacro de copia.
#
#   ./scripts/instalar-drill-mensual.sh          instala el cron del día 1
#   ./scripts/instalar-drill-mensual.sh --quitar lo desinstala
#
# Qué hace: añade UNA línea al crontab del Mac para que el día 1 de cada mes,
# a las 09:05, verifique la última copia con `deploy/backup.sh --drill`. No
# necesita sudo: el crontab es del usuario.
#
# El simulacro NO restaura nada de producción: descomprime la copia en un
# temporal, comprueba el MANIFEST, la integridad de SQLite y las filas. Si
# falla, deja el detalle en ~/respaldos/jarvis/drill.log.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${HOME}/respaldos/jarvis/drill.log"
MARCA="# jarvis-drill-mensual"

LINEA="5 9 1 * * cd '$HERE' && ./deploy/backup.sh --drill >> '$LOG' 2>&1 $MARCA"

if [ "${1:-}" = "--quitar" ]; then
  (crontab -l 2>/dev/null | grep -vF "$MARCA" || true) | crontab -
  echo "✔ simulacro mensual quitado del crontab"
  exit 0
fi

mkdir -p "$(dirname "$LOG")"
actual="$(crontab -l 2>/dev/null | grep -vF "$MARCA" || true)"
{
  [ -n "$actual" ] && printf '%s\n' "$actual"
  printf '%s\n' "$LINEA"
} | crontab -

echo "✔ simulacro mensual instalado (día 1 a las 09:05)"
echo "  log: $LOG"
echo "  quitar: $0 --quitar"
echo
echo "Pruébalo ahora sin esperar al mes:"
echo "  cd '$HERE' && ./deploy/backup.sh --drill"
