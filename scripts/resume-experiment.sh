#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON="${DPF_PYTHON:-$ROOT/.venv/bin/python}"

usage() {
    cat <<'EOF'
Usage: scripts/resume-experiment.sh --workspace PATH [options]

The source/target/driver request is read from WORKSPACE/.dpf/project.json.
Optional catalogs are needed only if the run stopped before candidate
resolution froze its catalog input.  Other options are execution overrides.

  --catalog PATH       Catalog; repeatable when candidate resolution is pending
  --model NAME         Codex model override
  --context-policy NAME  persistent or implementation-handoff; saved in run
  --codex-bin PATH     Codex executable; default: codex
  --backend NAME       Backend; default: exec
  --skill-root PATH    Explicit Skill override; normally omit
  -h, --help           Show this help
EOF
}

workspace=""
model=""
context_policy=""
codex_bin="codex"
backend="exec"
skill_root=""
catalogs=()
while (($#)); do
    case "$1" in
        --workspace) [[ $# -ge 2 ]] || { echo "error: --workspace needs a value" >&2; exit 2; }; workspace="$2"; shift 2 ;;
        --catalog) [[ $# -ge 2 ]] || { echo "error: --catalog needs a value" >&2; exit 2; }; catalogs+=("$2"); shift 2 ;;
        --model) [[ $# -ge 2 ]] || { echo "error: --model needs a value" >&2; exit 2; }; model="$2"; shift 2 ;;
        --context-policy) [[ $# -ge 2 ]] || { echo "error: --context-policy needs a value" >&2; exit 2; }; context_policy="$2"; shift 2 ;;
        --codex-bin) [[ $# -ge 2 ]] || { echo "error: --codex-bin needs a value" >&2; exit 2; }; codex_bin="$2"; shift 2 ;;
        --backend) [[ $# -ge 2 ]] || { echo "error: --backend needs a value" >&2; exit 2; }; backend="$2"; shift 2 ;;
        --skill-root) [[ $# -ge 2 ]] || { echo "error: --skill-root needs a value" >&2; exit 2; }; skill_root="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done
[[ -n "$workspace" ]] || { echo "error: --workspace is required" >&2; exit 2; }
config="$workspace/.dpf/project.json"
[[ -f "$config" ]] || { echo "error: no project config: $config" >&2; exit 2; }
[[ -x "$PYTHON" ]] || { echo "error: Python interpreter not found: $PYTHON" >&2; exit 2; }

mapfile -d '' -t request < <("$PYTHON" - "$config" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
for key in ("source_platform", "target_platform", "driver_name"):
    value = data.get(key)
    if not value:
        raise SystemExit(f"project.json is missing {key}")
    print(value, end="\0")
PY
)

args=(
    --workspace "$workspace"
    --source-platform "${request[0]}"
    --target-platform "${request[1]}"
    --driver-name "${request[2]}"
    --backend "$backend"
    --codex-bin "$codex_bin"
)
[[ -n "$model" ]] && args+=(--model "$model")
[[ -n "$context_policy" ]] && args+=(--context-policy "$context_policy")
[[ -n "$skill_root" ]] && args+=(--skill-root "$skill_root")
for path in "${catalogs[@]}"; do args+=(--catalog "$path"); done
exec "$ROOT/scripts/run-experiment.sh" "${args[@]}"
