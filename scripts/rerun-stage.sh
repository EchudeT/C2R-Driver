#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if (($# < 3)); then
    echo 'Usage: scripts/rerun-stage.sh WORKSPACE STAGE "reason" [resume options]' >&2
    exit 2
fi
workspace="$1"
stage="$2"
reason="$3"
shift 3
"$ROOT/scripts/reopen-stage.sh" "$workspace" "$stage" "$reason"
exec "$ROOT/scripts/resume-experiment.sh" --workspace "$workspace" "$@"
