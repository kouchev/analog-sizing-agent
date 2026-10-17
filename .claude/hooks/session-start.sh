#!/bin/bash
# Claude Code cloud sessions: build the venv and SG13G2 toolchain so tests and simulations run.
# The container is cached after this hook, so the long first build happens only once.
set -euo pipefail
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi
cd "$CLAUDE_PROJECT_DIR"
./scripts/bootstrap.sh
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export PATH=\"$CLAUDE_PROJECT_DIR/.venv/bin:\$PATH\""
    echo "export PDK_ROOT=\"$CLAUDE_PROJECT_DIR/.tools/IHP-Open-PDK\""
    echo "export NGSPICE=\"$CLAUDE_PROJECT_DIR/.tools/ngspice/bin/ngspice\""
  } >> "$CLAUDE_ENV_FILE"
fi
