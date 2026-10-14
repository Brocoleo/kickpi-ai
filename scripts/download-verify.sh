#!/usr/bin/env bash
# Descarga + verificación SHA256 (Fase 3). Uso:
# ./scripts/download-verify.sh "<url-img.xz>" "<url-sha>"
set -euo pipefail
[ $# -eq 2 ] || { echo "Uso: $0 <url-img.xz> <url-sha>"; exit 1; }
URL_IMG="$1"; URL_SHA="$2"
mkdir -p dist
cd dist
echo "Descargando imagen..."
curl -fSL -o image.img.xz "$URL_IMG"
echo "Descargando sha..."
curl -fSL -o image.img.xz.sha "$URL_SHA"
echo "--- SHA remoto ---"; cat image.img.xz.sha
echo "--- Verificando ---"
sha256sum -c image.img.xz.sha
echo "OK. Archivo: dist/image.img.xz"
ls -lh image.img.xz
