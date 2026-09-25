#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON="${DPF_PYTHON:-$ROOT/.venv/bin/python}"
if (($# < 1)); then
    echo "Usage: scripts/status.sh WORKSPACE [--json] [--pricing-model MODEL] [--pricing-tier TIER]" >&2
    exit 2
fi
workspace="$1"
shift
[[ -x "$PYTHON" ]] || { echo "error: Python interpreter not found: $PYTHON" >&2; exit 2; }
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m driver_port_factory.cli status "$workspace" "$@"
