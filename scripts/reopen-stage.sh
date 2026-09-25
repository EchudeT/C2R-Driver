#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON="${DPF_PYTHON:-$ROOT/.venv/bin/python}"
if (($# < 3)); then
    echo 'Usage: scripts/reopen-stage.sh WORKSPACE STAGE "reason"' >&2
    exit 2
fi
workspace="$1"
stage="$2"
reason="$3"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m driver_port_factory.cli stage reopen "$workspace" "$stage" --reason "$reason"
