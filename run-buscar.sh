#!/usr/bin/env bash
# Corre el buscador con las vars del .env cargadas. Para cron o manual.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$HERE/.env" ]; then set -a; source "$HERE/.env"; set +a; fi
exec python3 "$HERE/buscar.py"
