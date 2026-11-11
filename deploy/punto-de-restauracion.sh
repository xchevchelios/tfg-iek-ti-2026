#!/usr/bin/env bash
# Guarda un punto de restauracion del backend ANTES de desplegar un cambio.
#
#   bash deploy/punto-de-restauracion.sh [nombre]
#
# Guarda en ~/tfg-respaldos/<nombre>/:
#   - el commit actual (y una etiqueta git local restauracion/<nombre>)
#   - api/.env
#   - una copia consistente de la base SQLite (sirve con los servicios andando)
#   - copias de las units de systemd y de la config de nginx, como referencia
#   - cualquier cambio local sin commitear, como parche
#
# No detiene nada ni modifica el sistema. Para volver:
#   bash ~/tfg-respaldos/<nombre>/restaurar.sh <nombre>
set -euo pipefail

# El repo es el de este script, o el del directorio actual si se lo corre desde
# una copia suelta (por ejemplo, la primera vez, antes de hacer git pull).
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$AQUI" rev-parse --show-toplevel 2>/dev/null || git rev-parse --show-toplevel)"
NOMBRE="${1:-pre-$(date +%Y%m%d-%H%M)}"
DESTINO="$HOME/tfg-respaldos/$NOMBRE"

if [[ -e "$DESTINO" ]]; then
  echo "Ya existe $DESTINO. Elegi otro nombre." >&2
  exit 1
fi
mkdir -p "$DESTINO"

# --- Codigo ---
COMMIT="$(git -C "$REPO" rev-parse HEAD)"
echo "$COMMIT" > "$DESTINO/commit.txt"
echo "$REPO" > "$DESTINO/repo.txt"
# Copia del script de restauracion junto al respaldo: el commit viejo puede no
# tenerlo, y asi siempre se puede volver aunque el repo cambie.
if [[ -f "$AQUI/restaurar.sh" ]]; then
  cp "$AQUI/restaurar.sh" "$DESTINO/restaurar.sh"
else
  git -C "$REPO" show origin/main:deploy/restaurar.sh > "$DESTINO/restaurar.sh"
fi
git -C "$REPO" tag -f "restauracion/$NOMBRE" "$COMMIT" >/dev/null
if ! git -C "$REPO" diff --quiet HEAD; then
  git -C "$REPO" diff HEAD > "$DESTINO/cambios-locales.patch"
  echo "! Hay cambios locales sin commitear: guardados en cambios-locales.patch"
fi

# --- Configuracion ---
cp -p "$REPO/api/.env" "$DESTINO/api.env"

# --- Base de datos ---
DB_PATH="$(grep -E '^DB_PATH=' "$REPO/api/.env" | tail -1 | cut -d= -f2- || true)"
DB_PATH="${DB_PATH:-$REPO/api/sensores.db}"
echo "$DB_PATH" > "$DESTINO/db_path.txt"
PY="$REPO/api/venv/bin/python"
[[ -x "$PY" ]] || PY=python3
# La API de backup de SQLite copia una foto consistente aunque la ingesta este
# escribiendo (modo WAL). Copiar el archivo con cp podria dejar una base rota.
"$PY" - "$DB_PATH" "$DESTINO/sensores.db" <<'EOF'
import sqlite3, sys
origen = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
destino = sqlite3.connect(sys.argv[2])
origen.backup(destino)
filas = destino.execute("SELECT COUNT(*) FROM mediciones").fetchone()[0]
destino.close(); origen.close()
print(f"  base: {filas} mediciones copiadas")
EOF

# --- Referencias (solo lectura, por si un cambio futuro toca esto) ---
mkdir -p "$DESTINO/referencia"
cp -p /etc/systemd/system/tfg-*.service "$DESTINO/referencia/" 2>/dev/null || true
cp -p /etc/nginx/sites-enabled/* "$DESTINO/referencia/" 2>/dev/null || true
cp -p /etc/nginx/nginx.conf "$DESTINO/referencia/" 2>/dev/null || true

echo
echo "Punto de restauracion '$NOMBRE' listo en $DESTINO"
echo "  commit: $(git -C "$REPO" log -1 --format='%h %s' "$COMMIT")"
echo "Para volver a este estado:  bash $DESTINO/restaurar.sh $NOMBRE"
