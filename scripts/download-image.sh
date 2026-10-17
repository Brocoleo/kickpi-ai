#!/usr/bin/env bash
# Descarga la imagen compilada y VERIFICA su SHA256 antes de usarla.
#
# Uso:
#   ./scripts/download-image.sh "<url-completa-del-.img.xz>"
#   ./scripts/download-image.sh latest          # último release con imagen K2B
#   ./scripts/download-image.sh k2b-v2-20261017 # release por tag
#
# Solo acepta assets cuyo nombre contenga MATCH (por defecto kickpi-k2b-v2):
# en el repo hay releases antiguos (p. ej. la imagen Armbian que NO sirve para
# esta placa) y así nunca se cuela uno de ellos por error. Con MATCH="" se
# acepta cualquier *.img.xz.
#
# Deja los ficheros en ./dist/ y comprueba el checksum contra el .sha256
# publicado junto a la imagen.
set -euo pipefail

REPO="${REPO:-Brocoleo/kickpi-ai}"
DEST="${DEST:-dist}"
MATCH="${MATCH-kickpi-k2b-v2}"
EXCLUDE="${EXCLUDE-armbian}"
ARG="${1:-}"

die(){ echo "error: $*" >&2; exit 1; }
[[ -n "$ARG" ]] || die "uso: $0 <url-del-.img.xz | latest | tag>"

mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"

resolve_asset(){
	# $1 = tag ("latest" o un tag concreto) -> imprime URLs de descarga directa
	local tag="$1" json="" t="" names=""
	# Con gh autenticado no hace falta la API pública (usa plantillas de Go,
	# sin depender de jq; y construye URLs de descarga, no de la API).
	if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
		if [[ "$tag" == "latest" ]]; then
			t="$(gh release view --repo "$REPO" --json tagName --template '{{.tagName}}' 2>/dev/null || true)"
		else
			t="$tag"
		fi
		if [[ -n "$t" ]]; then
			names="$(gh release view "$t" --repo "$REPO" --json assets \
				--template '{{range .assets}}{{.name}}{{"\n"}}{{end}}' 2>/dev/null || true)"
			if [[ -n "$names" ]]; then
				printf '%s\n' "$names" | sed "s|^|https://github.com/${REPO}/releases/download/${t}/|"
				return
			fi
		fi
	fi
	if [[ "$tag" == "latest" ]]; then
		json="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest")"
	else
		json="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/tags/${tag}")"
	fi
	printf '%s' "$json" | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(a["browser_download_url"]) for a in d.get("assets",[])]'
}

list_tags(){
	# tags de los releases, del más nuevo al más viejo
	if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
		gh release list --repo "$REPO" --limit 50 --json tagName,createdAt \
			--template '{{range .}}{{.createdAt}} {{.tagName}}{{"\n"}}{{end}}' 2>/dev/null \
			| sort -r | awk '{print $2}'
		return
	fi
	curl -fsSL "https://api.github.com/repos/${REPO}/releases?per_page=50" \
		| python3 -c 'import json,sys; [print(r["tag_name"]) for r in json.load(sys.stdin)]' 2>/dev/null
}

pick_img(){
	# imprime la primera URL de un *.img.xz que cumpla MATCH (y no EXCLUDE)
	local urls="$1" u
	while IFS= read -r u; do
		[[ -n "$u" ]] || continue
		[[ -n "$EXCLUDE" && "${u,,}" == *"$EXCLUDE"* ]] && continue
		[[ -n "$MATCH" && "$u" != *"$MATCH"* ]] && continue
		printf '%s\n' "$u"; return 0
	done < <(printf '%s\n' "$urls" | grep -E '\.img\.xz$' || true)
	return 1
}

if [[ "$ARG" =~ ^https?:// ]]; then
	IMG_URL="$ARG"
else
	echo "==> Buscando release '$ARG' en $REPO"
	IMG_URL=""
	for cand in $( [[ "$ARG" == "latest" ]] && list_tags || echo "$ARG" ); do
		echo "    revisando '$cand'..."
		ASSETS="$(resolve_asset "$cand" || true)"
		[[ -n "$ASSETS" ]] || continue
		IMG_URL="$(pick_img "$ASSETS" || true)"
		[[ -n "$IMG_URL" ]] && break
	done
	[[ -n "$IMG_URL" ]] || die "no hay ningún release con un asset *.img.xz${MATCH:+ que contenga '$MATCH'}"
fi

NAME="$(basename "${IMG_URL%%\?*}")"
SHA_URL="${IMG_URL}.sha256"
echo "==> Imagen : $IMG_URL"
echo "==> Destino: $DEST/$NAME"

curl -fSL --progress-bar -o "$DEST/$NAME" "$IMG_URL"
echo "==> Descargando checksum..."
if ! curl -fsSL -o "$DEST/$NAME.sha256" "$SHA_URL"; then
	die "no se pudo descargar $SHA_URL (¿existe el checksum en el release?)"
fi
echo "--- checksum publicado ---"
cat "$DEST/$NAME.sha256"

echo "--- verificando SHA256 ---"
( cd "$DEST" && sha256sum -c "$NAME.sha256" )

echo
echo "==> OK: $(ls -lh "$DEST/$NAME" | awk '{print $5}') en $DEST/$NAME"
