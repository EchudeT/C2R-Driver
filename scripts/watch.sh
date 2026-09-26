#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if (($# == 1)) && [[ "$1" == "-h" || "$1" == "--help" ]]; then
    echo "Usage: scripts/watch.sh WORKSPACE [--interval SECONDS] [--costs] [status options]"
    exit 0
fi
if (($# < 1)); then
    echo "Usage: scripts/watch.sh WORKSPACE [--interval SECONDS] [--costs] [status options]" >&2
    exit 2
fi
workspace="$1"
shift
interval=5
mode=status
status_args=()
while (($#)); do
    case "$1" in
        --interval|-n) [[ $# -ge 2 ]] || { echo "error: interval needs a value" >&2; exit 2; }; interval="$2"; shift 2 ;;
        --costs) mode=costs; shift ;;
        *) status_args+=("$1"); shift ;;
    esac
done
if [[ "$mode" == costs ]]; then
    command=("$ROOT/scripts/costs.py" "$workspace" "${status_args[@]}")
else
    command=("$ROOT/scripts/status.sh" "$workspace" "${status_args[@]}")
fi
command -v watch >/dev/null 2>&1 || { echo "error: watch is not installed" >&2; exit 2; }
exec watch -n "$interval" -x -- "${command[@]}"
