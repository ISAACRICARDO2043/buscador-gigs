#!/usr/bin/env bash
# Instala/actualiza el timer --user de buscador-gigs en el M700. Idempotente, sin sudo.
# Uso: bash deploy/install-m700.sh   (desde el clone en ~/proyectos-cliente/buscador-gigs)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNITS="$HOME/.config/systemd/user"
mkdir -p "$UNITS"
ln -sf "$HERE/buscador-gigs.service" "$UNITS/buscador-gigs.service"
ln -sf "$HERE/buscador-gigs.timer"   "$UNITS/buscador-gigs.timer"
ln -sf "$HERE/buscador-gigs-resumen.service" "$UNITS/buscador-gigs-resumen.service"   # 007
ln -sf "$HERE/buscador-gigs-resumen.timer"   "$UNITS/buscador-gigs-resumen.timer"
systemctl --user daemon-reload
systemctl --user enable --now buscador-gigs.timer buscador-gigs-resumen.timer
systemctl --user list-timers --no-pager | grep buscador-gigs
