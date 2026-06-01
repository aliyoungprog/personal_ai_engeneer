#!/bin/sh
# Sync the host's live Claude Code OAuth token (macOS Keychain, auto-refreshed
# while Claude Code is used on the host) into the ai-agent container volume.
#
# The container cannot refresh the OAuth token itself; on expiry the coder run
# 401s. Run this on a schedule (launchd) so the container stays authenticated.
# Long-term: switch the container to an ANTHROPIC_API_KEY to remove this entirely.
set -eu

DOCKER=/usr/local/bin/docker
SECURITY=/usr/bin/security
CONTAINER=ai-agent
DEST=/home/agent/.claude/.credentials.json
LOG=/tmp/pae-token-sync.log

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $1" >>"$LOG"; }

# Skip quietly if the container isn't running.
if ! "$DOCKER" inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
    log "skip: container $CONTAINER not running"
    exit 0
fi

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

if ! "$SECURITY" find-generic-password -s "Claude Code-credentials" -w >"$TMP" 2>/dev/null; then
    log "error: could not read Keychain token"
    exit 1
fi

"$DOCKER" cp "$TMP" "$CONTAINER:$DEST"
"$DOCKER" exec -u root "$CONTAINER" sh -c "chown agent:agent $DEST && chmod 600 $DEST"
log "synced token into $CONTAINER"
