#!/usr/bin/env bash
# =============================================================================
# cerrar-filebrowser.sh — deja FileBrowser accesible SOLO a través de Jarvis.
#
# Se ejecuta EN LA PLACA, con sudo (Tailscale es del sistema):
#
#     ssh -t leandro@100.111.200.54 \
#       'sudo bash /home/leandro/jarvis/deploy/remote/cerrar-filebrowser.sh'
#
# Qué hace y por qué:
#   - Apaga el `tailscale serve` del puerto 8443. FileBrowser en modo noauth
#     usa el usuario 1 (admin); si queda expuesto en el tailnet, cualquier
#     dispositivo de la tailnet puede entrar a sus ajustes sin login.
#   - El puerto del host queda en 127.0.0.1:8080, así que el agente sigue
#     pudiendo proxear /files/* con sesión de Jarvis.
#   - No se toca el contenedor ni la configuración: es reversible con
#     `tailscale serve --bg --https=8443 http://127.0.0.1:8080`.
#
# Si usas el enlace directo :8443 en algún sitio, este script te lo quita.
# La pestaña Archivos de la PWA no depende de él.
# =============================================================================
set -euo pipefail

verde() { printf '\033[32m%s\033[0m\n' "$*"; }
ambar() { printf '\033[33m%s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Este script necesita sudo (Tailscale y el serve son del sistema)." >&2
  exit 1
fi

if ! command -v tailscale >/dev/null 2>&1; then
  echo "No encuentro tailscale en PATH." >&2
  exit 1
fi

# El contenedor debe seguir sano: cerramos la puerta, no el servicio.
if ! curl -fsS --max-time 5 http://127.0.0.1:8080/files/health >/dev/null; then
  echo "FileBrowser no responde en 127.0.0.1:8080; no se toca Tailscale." >&2
  exit 1
fi

ambar "· estado actual de Tailscale Serve:"
tailscale serve status || true

# Apagar el 8443 si existe. Si no existe, `off` no es un error.
tailscale serve --https=8443 off >/dev/null 2>&1 || true

ambar "· estado después:"
tailscale serve status || true

# Comprobación final: el proxy interno sigue respondiendo.
if curl -fsS --max-time 5 http://127.0.0.1:8080/files/health >/dev/null; then
  verde "✔ FileBrowser ya no está expuesto en :8443 y sigue sano en localhost"
  echo "  Acceso normal: https://kickpi-jarvis.tail1e3b4d.ts.net/files/"
else
  echo "✗ FileBrowser dejó de responder en localhost; revisa el contenedor" >&2
  exit 1
fi
