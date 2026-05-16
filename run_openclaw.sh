#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONUTF8=1

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

MODE="upcoming"
DAYS="45"
PLAYS="all"
AI=""
ALERTS=""
JSON=""
MATCHES=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="$2"
      shift 2
      ;;
    --days)
      DAYS="$2"
      shift 2
      ;;
    --plays)
      PLAYS="$2"
      shift 2
      ;;
    --ai)
      AI="--ai"
      shift
      ;;
    --alerts)
      ALERTS="--alerts"
      shift
      ;;
    --json)
      JSON="--json"
      shift
      ;;
    --matches)
      MATCHES="--matches $2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

"$PYTHON_BIN" main.py --mode "$MODE" --days "$DAYS" --plays "$PLAYS" $MATCHES $AI $ALERTS $JSON
