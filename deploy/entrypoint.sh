#!/bin/sh
set -e

# Restore Claude Code config file if missing.
# Claude stores .credentials.json inside ~/.claude/ (mounted volume) but
# expects .claude.json one level up at $HOME/.claude.json — outside the volume.
# On every container start we re-create it from the volume backups if missing.
if [ ! -f /home/agent/.claude.json ]; then
    latest_backup=$(ls -t /home/agent/.claude/backups/.claude.json.backup.* 2>/dev/null | head -1)
    if [ -n "$latest_backup" ]; then
        echo "[entrypoint] Restoring .claude.json from $latest_backup"
        cp "$latest_backup" /home/agent/.claude.json
    else
        echo '[entrypoint] Initializing empty .claude.json'
        printf '{"firstStartTime":"%s"}' "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)" > /home/agent/.claude.json
    fi
fi

echo "[entrypoint] Running aerich upgrade..."
aerich upgrade || {
    echo "[entrypoint] aerich upgrade failed" >&2
    exit 1
}

echo "[entrypoint] Starting ai_agent.main"
exec python -m ai_agent.main
