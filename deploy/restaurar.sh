#!/usr/bin/env bash
# Vuelve el backend a un punto guardado con deploy/punto-de-restauracion.sh.
#
#   bash ~/tfg-respaldos/<nombre>/restaurar.sh <nombre>              # codigo + .env
#   bash ~/tfg-respaldos/<nombre>/restaurar.sh <nombre> --con-base   # ademas, la base
#
# Por defecto NO toca la base: los cambios de esquema de este proyecto son
# aditivos (tablas nuevas), y el codigo viejo simplemente las ignora. Restaurar
# la base pierde todas las mediciones recibidas desde el punto de restauracion,
# asi que solo tiene sentido si la base quedo danada. La base actual no se
# borra: se aparta junto al respaldo.
#
# No toca Mosquitto, nginx, el firmware ni el frontend de Cloudflare.
set -euo pipefail

# Bash lee el script a medida que lo ejecuta. Si este archivo es el del repo y
# el checkout lo cambia o lo borra, el resto se ejecutaria mal. Por eso se
# relanza desde una copia temporal.
if [[ -z "${TFG_RESTAURAR_COPIA:-}" ]]; then
  COPIA="$(mktemp)"
  cp "${BASH_SOURCE[0]}" "$COPIA"
  TFG_RESTAURAR_COPIA="$COPIA" exec bash "$COPIA" "$@"
fi
trap 'rm -f "$TFG_RESTAURAR_COPIA"' EXIT

NOMBRE="${1:-}"
CON_BASE=0
if [[ "${2:-}" == "--con-base" ]]; then CON_BASE=1; fi

if [[ -z "$NOMBRE" ]]; then
  echo "Uso: bash restaurar.sh <nombre> [--con-base]" >&2
  echo "Puntos disponibles:" >&2
  ls -1 "$HOME/tfg-respaldos" 2>/dev/null >&2 || echo "  (ninguno)" >&2
  exit 1
fi
ORIGEN="$HOME/tfg-respaldos/$NOMBRE"
[[ -f "$ORIGEN/commit.txt" ]] || { echo "No existe el punto '$NOMBRE' en $ORIGEN" >&2; exit 1; }

COMMIT="$(cat "$ORIGEN/commit.txt")"
REPO="$(cat "$ORIGEN/repo.txt")"
DB_PATH="$(cat "$ORIGEN/db_path.txt")"

if ! git -C "$REPO" diff --quiet HEAD; then
  echo "Hay cambios locales sin commitear en $REPO." >&2
  echo "Guardalos (git stash) o descartalos antes de restaurar." >&2
  exit 1
fi

echo "Se va a volver a:"
echo "  commit  $(git -C "$REPO" log -1 --format='%h %s (%cd)' --date=short "$COMMIT")"
echo "  .env    $ORIGEN/api.env"
if (( CON_BASE )); then
  echo "  base    $ORIGEN/sensores.db"
  echo "  ATENCION: se pierden las mediciones recibidas desde que se creo el punto."
fi
read -r -p "¿Continuar? [s/N] " RESPUESTA
[[ "$RESPUESTA" =~ ^[sS]$ ]] || { echo "Cancelado."; exit 0; }

echo "Deteniendo servicios..."
sudo systemctl stop tfg-ingesta tfg-api

git -C "$REPO" checkout --quiet --detach "$COMMIT"
cp -p "$ORIGEN/api.env" "$REPO/api/.env"

if (( CON_BASE )); then
  APARTADA="$ORIGEN/base-reemplazada-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$APARTADA"
  for f in "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"; do
    if [[ -e "$f" ]]; then mv "$f" "$APARTADA/"; fi
  done
  cp -p "$ORIGEN/sensores.db" "$DB_PATH"
  echo "  base actual apartada en $APARTADA"
fi

echo "Arrancando servicios..."
sudo systemctl start tfg-api tfg-ingesta
sleep 3
systemctl --no-pager --lines=0 status tfg-api tfg-ingesta | grep -E '●|Active:' || true
echo
journalctl -u tfg-ingesta --no-pager -n 5 || true

cat <<EOF

Listo. El repo quedo en el commit del punto de restauracion, sin rama
(detached HEAD), para que un git pull no vuelva a traer el cambio.

Cuando el cambio este corregido o revertido en GitHub:
  git -C "$REPO" switch main && git -C "$REPO" pull
y reiniciar:  sudo systemctl restart tfg-api tfg-ingesta
EOF
