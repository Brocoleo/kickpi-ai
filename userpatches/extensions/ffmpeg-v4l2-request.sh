# Override local para kickpi-k2b-v2 en GitHub Actions.
# El framework busca primero en userpatches/extensions/ y este archivo
# tiene precedencia sobre extensions/ffmpeg-v4l2-request.sh del framework.
# Bug upstream: el original hace `declare -g PACKAGE_LIST_BOARD+=...`
# pero PACKAGE_LIST_BOARD ya es readonly desde kickpi-k2b-v2.csc -> Error 1.
# Para servidor headless no necesitamos el pack FFmpeg HW prebuilt (~40MB),
# así que ambas fases solo avisan y continúan sin tocar PACKAGE_LIST_BOARD.
function extension_prepare_config__ffmpeg_v4l2_request() {
	display_alert "ffmpeg-v4l2-request" "override local: sin pack prebuilt para k2b-v2 (bug readonly), continuando" "warn"
	return 0
}

function post_family_tweaks__ffmpeg_v4l2_request() {
	display_alert "ffmpeg-v4l2-request" "override local: instalación omitida" "warn"
	return 0
}
