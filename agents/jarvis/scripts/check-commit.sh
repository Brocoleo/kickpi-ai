#!/usr/bin/env bash
# =============================================================================
# check-commit.sh — Un commit se entiende sin abrir el código.
#
# La regla: título que dice QUÉ cambia y cuerpo que dice POR QUÉ, qué se
# tocó y cómo se comprobó. Dentro de un año, el `git log` tiene que contar
# la historia del proyecto sin ayuda de nadie.
#
# Uso:
#   ./scripts/check-commit.sh .git/COMMIT_EDITMSG   # lo llama el hook
#   ./scripts/check-commit.sh --last                # revisa el último commit
#   ./scripts/check-commit.sh --install             # activa el hook (una vez)
#   git log -1 --format=%B | ./scripts/check-commit.sh -
#
# Formato:
#
#   tipo(ámbito): resumen en minúsculas, sin punto final   (≤ 72 columnas)
#
#   Resumen
#   Qué cambia, en dos o tres frases.
#
#   Causa
#   El problema real, no el síntoma.
#
#   Qué cambió
#   Los archivos o decisiones que importan.
#
#   Pruebas
#   Qué se ejecutó y qué salió.
#
# Los tipos son los de siempre: feat, fix, chore, docs, refactor, test, perf,
# build, ci, ops. `chore` y `docs` pueden llevar un cuerpo más corto.
#
# Se saltan: merges, reverts y mensajes de `git commit --fixup`.
# =============================================================================
set -uo pipefail

TITULO_MAX=72
CUERPO_MIN=120
SECCIONES=(Resumen Causa "Qué cambió" Pruebas)

rojo() { printf '\033[31m%s\033[0m\n' "$*" >&2; }
ambar() { printf '\033[33m%s\033[0m\n' "$*" >&2; }

mostrar_plantilla() {
  cat >&2 <<'TXT'

Formato esperado:

  tipo(ámbito): resumen en minúsculas, sin punto final

  Resumen
  Qué cambia, en dos o tres frases.

  Causa
  El problema real, no el síntoma.

  Qué cambió
  Los archivos o decisiones que importan.

  Pruebas
  Qué se ejecutó y qué salió.

TXT
}

# La instalación no es un mensaje: se atiende antes de leer nada (si se
# hiciera dentro de `leer_mensaje`, el exit solo saldría de la subshell).
if [ "${1:-}" = "--install" ]; then
  # El repo es más grande que este proyecto (Jarvis vive en un
  # subdirectorio), así que la ruta del hook se calcula respecto a la raíz
  # del repo y no respecto al directorio actual.
  raiz="$(git rev-parse --show-toplevel)"
  prefijo="$(git rev-parse --show-prefix)"
  hook="$raiz/${prefijo}scripts/git-hooks/commit-msg"
  if [ ! -x "$hook" ]; then
    rojo "falta ${prefijo}scripts/git-hooks/commit-msg o no es ejecutable"
    exit 1
  fi
  git config core.hooksPath "${prefijo}scripts/git-hooks"
  printf '\033[32m%s\033[0m\n' "hook instalado: core.hooksPath=${prefijo}scripts/git-hooks"
  exit 0
fi

leer_mensaje() {
  case "${1:-}" in
    --last)
      git log -1 --format=%B
      ;;
    "" | "-")
      cat
      ;;
    *)
      cat "$1"
      ;;
  esac
}

mensaje="$(leer_mensaje "${1:-}")"
if [ -z "${mensaje//[[:space:]]/}" ]; then
  rojo "mensaje de commit vacío"
  exit 1
fi

# Quita comentarios del editor y líneas finales en blanco.
limpio="$(printf '%s\n' "$mensaje" | sed '/^#/d' | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}')"

titulo="$(printf '%s\n' "$limpio" | head -n 1)"
cuerpo="$(printf '%s\n' "$limpio" | tail -n +2)"

# Casos que no se revisan.
case "$titulo" in
  Merge\ * | Revert\ * | fixup!\ * | squash!\ *) exit 0 ;;
esac

fallos=0
fallo() {
  rojo "✗ $*"
  fallos=$((fallos + 1))
}

# --- título -----------------------------------------------------------------
if ! printf '%s' "$titulo" | grep -Eq '^(feat|fix|chore|docs|refactor|test|perf|build|ci|ops)(\([a-z0-9._/-]+\))?: .+'; then
  fallo "el título no empieza por tipo(ámbito): …"
fi

if [ "${#titulo}" -gt "$TITULO_MAX" ]; then
  fallo "el título tiene ${#titulo} columnas (máximo $TITULO_MAX)"
fi

if printf '%s' "$titulo" | grep -q '\.$'; then
  fallo "el título no lleva punto final"
fi

if printf '%s' "$titulo" | grep -Eq '^[^:]+: [A-ZÁÉÍÓÚÑ]'; then
  ambar "· el resumen empieza en mayúscula: en este proyecto va en minúsculas"
fi

# --- cuerpo -----------------------------------------------------------------
if [ -z "${cuerpo//[[:space:]]/}" ]; then
  fallo "falta el cuerpo: un título no explica el porqué"
else
  largo="$(printf '%s' "$cuerpo" | tr -d '[:space:]' | wc -c | tr -d ' ')"
  # Tareas de mantenimiento pueden ser breves; una funcionalidad no.
  tipo_titulo="${titulo%%:*}"
  tipo_titulo="${tipo_titulo%%(*}"
  minimo="$CUERPO_MIN"
  case "$tipo_titulo" in
    chore | docs | ci | build | test) minimo=60 ;;
  esac
  if [ "$largo" -lt "$minimo" ]; then
    fallo "el cuerpo es demasiado corto ($largo caracteres, mínimo $minimo)"
  fi

  # La segunda línea debe estar en blanco: si no, el título se come el cuerpo.
  segunda="$(printf '%s\n' "$cuerpo" | head -n 1)"
  if [ -n "${segunda//[[:space:]]/}" ]; then
    fallo "falta la línea en blanco entre el título y el cuerpo"
  fi

  tipo="${titulo%%:*}"
  tipo="${tipo%%(*}"
  case "$tipo" in
    feat | fix | perf | refactor)
      for seccion in "${SECCIONES[@]}"; do
        if ! printf '%s' "$cuerpo" | grep -Eq "^[[:space:]]*${seccion}[[:space:]]*$"; then
          fallo "falta la sección «${seccion}»"
        fi
      done
      ;;
  esac
fi

if [ "$fallos" -gt 0 ]; then
  rojo "commit rechazado ($fallos problema(s))"
  mostrar_plantilla
  exit 1
fi

exit 0
