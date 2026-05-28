#!/bin/sh
set -e

echo "[entrypoint] Running aerich upgrade..."
aerich upgrade || {
    echo "[entrypoint] aerich upgrade failed; if this is the first run with no migrations check repo" >&2
    exit 1
}

echo "[entrypoint] Starting ai_agent.main"
exec python -m ai_agent.main
