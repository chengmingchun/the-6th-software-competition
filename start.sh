#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PLAYER_ID="${1:-player0}"
HOST="${2:-}"
PORT="${3:-}"

exec python3 main.py "$PLAYER_ID" "$HOST" "$PORT"
