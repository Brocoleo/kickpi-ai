#!/usr/bin/env bash
# =============================================================================
# rebuild.sh — se ejecuta EN LA PLACA (con sudo), invocado por deploy-all.sh.
#
# Por qué existe este archivo aparte: así el bloque que necesita sudo es un
# único comando corto por SSH, sin comillas anidadas ni heredocs frágiles.
#
# Qué hace: reconstruye los servicios pedidos, los recrea y **espera a que
# estén SANOS** antes de terminar. Sin la espera, el script de despliegue
# terminaba en falso: `docker compose up -d` vuelve en cuanto el contenedor
# arranca, pero el healthcheck aún dice "starting".
#
# Uso: sudo bash deploy/remote/rebuild.sh agente [ejecutor|herramientas]
# =============================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$RAIZ"

SERVICIOS=()
for arg in "$@"; do
  case "$arg" in
    agente) SERVICIOS+=(jarvis-agent) ;;
    ejecutor) SERVICIOS+=(jarvis-executor) ;;
    herramientas) SERVICIOS+=(jarvis-tools) ;;
    *)
      echo "servicio desconocido: $arg (usa agente / ejecutor / herramientas)" >&2
      exit 2
      ;;
  esac
done

if [ "${#SERVICIOS[@]}" -eq 0 ]; then
  echo "nada que reconstruir" >&2
  exit 2
fi

estado_de() {
  docker inspect \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    "$1" 2>/dev/null || echo "inexistente"
}

for servicio in "${SERVICIOS[@]}"; do
  echo "== construyendo $servicio"
  docker compose build "$servicio"
done

echo "== recreando: ${SERVICIOS[*]}"
docker compose up -d "${SERVICIOS[@]}"

# Espera activa: hasta 80 s por contenedor, informando solo de los cambios.
for servicio in "${SERVICIOS[@]}"; do
  anterior=""
  sano=0
  for _ in $(seq 1 40); do
    actual="$(estado_de "$servicio")"
    if [ "$actual" != "$anterior" ]; then
      printf '   %-16s %s\n' "$servicio" "$actual"
      anterior="$actual"
    fi
    if [ "$actual" = "healthy" ]; then
      sano=1
      break
    fi
    sleep 2
  done
  if [ "$sano" -ne 1 ]; then
    echo "!! $servicio NO llegó a healthy (último estado: $anterior)" >&2
    echo "   logs:" >&2
    docker logs --tail 30 "$servicio" >&2 || true
    exit 1
  fi
done

# El ejecutor no publica puerto, así que su salud se prueba dentro de la red
# de compose: el agente es el único que habla con él.
echo
echo "== estado final"
docker inspect \
  --format '   {{.Name}}: {{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
  "${SERVICIOS[@]}"
