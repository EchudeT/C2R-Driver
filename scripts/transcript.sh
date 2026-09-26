#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON="${DPF_PYTHON:-$ROOT/.venv/bin/python}"
if (($# < 2)); then
    echo "Usage: scripts/transcript.sh WORKSPACE STAGE [--include-content]" >&2
    exit 2
fi
workspace="$1"
stage="$2"
shift 2
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m driver_port_factory.cli codex transcript "$workspace" "$stage" "$@"
