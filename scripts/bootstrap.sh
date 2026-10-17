#!/usr/bin/env bash
# One-step setup after cloning:
#   ./scripts/bootstrap.sh              Python venv + SG13G2 toolchain (first run 15-20 min, then seconds)
#   ./scripts/bootstrap.sh --python     Python venv only (generic models; needs ngspice on PATH)
# Afterwards:  source .venv/bin/activate   (also sets PDK_ROOT and NGSPICE)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
log() { echo "[bootstrap] $*"; }

PY="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
  log "creating .venv"
  "$PY" -m venv .venv || { log "python venv module missing: install python3-venv"; exit 1; }
fi
log "installing the package, test and drawing tools into .venv"
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e ".[dev,draw]"

if [ "${1:-}" = "--python" ]; then
  log "done (Python only). Activate with: source .venv/bin/activate"
  exit 0
fi

PREFIX="$REPO/.tools" "$REPO/scripts/setup_sg13g2.sh"

# Make 'source .venv/bin/activate' also point the code at the repo's toolchain.
MARK="# analog-sizing-agent toolchain"
if ! grep -q "$MARK" .venv/bin/activate; then
  cat >> .venv/bin/activate <<EOF

$MARK
export PDK_ROOT="$REPO/.tools/IHP-Open-PDK"
export NGSPICE="$REPO/.tools/ngspice/bin/ngspice"
EOF
fi

log "checking the installation"
PDK_ROOT="$REPO/.tools/IHP-Open-PDK" NGSPICE="$REPO/.tools/ngspice/bin/ngspice" .venv/bin/python -m pytest -q

log "done. Activate with: source .venv/bin/activate"
