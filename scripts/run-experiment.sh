#!/usr/bin/env bash
set -euo pipefail

# Start one foreground controller.  Keeping this process in the caller's
# terminal makes Ctrl-C and process inspection predictable; use tmux/systemd
# explicitly if a detached run is wanted.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON="${DPF_PYTHON:-$ROOT/.venv/bin/python}"

usage() {
    cat <<'EOF'
Usage: scripts/run-experiment.sh --workspace PATH --driver-name NAME [options]

Required:
  --workspace PATH                  New or existing experiment workspace
  --driver-name NAME                Driver identity (for example e1000)

Request and execution options:
  --source-platform NAME             Default: linux
  --target-platform NAME             Default: asterinas
  --catalog PATH                     Driver catalog; repeat for multiple catalogs
  --model NAME                       Codex model override
  --context-policy NAME              persistent, analysis-handoff or implementation-handoff
  --codex-bin PATH                   Codex executable; default: codex
  --backend NAME                     Backend; default: exec
  --baseline-repository PATH         Read-only cache; repeatable
  --local-source-repository PATH     Existing Linux/source checkout
  --local-target-repository PATH     Existing Asterinas/target checkout
  --local-qemu-repository PATH       Existing QEMU checkout or bare repository
  --skill-root PATH                  Optional Skill override (normally omit)
  --analysis-review / --no-analysis-review
  --final-evidence-review / --no-final-evidence-review
  -h, --help                         Show this help

The controller stays in the foreground and resumes an existing workspace when
the same request is supplied again.  Use scripts/resume-experiment.sh for a
workspace whose request is already frozen.
EOF
}

if [[ ! -x "$PYTHON" ]]; then
    echo "error: Python interpreter not found or not executable: $PYTHON" >&2
    echo "create the environment with: python3.11 -m venv $ROOT/.venv" >&2
    exit 2
fi

workspace=""
source_platform="linux"
target_platform="asterinas"
driver_name=""
codex_bin="codex"
backend="exec"
model=""
context_policy=""
skill_root=""
catalogs=()
baselines=()
local_source=""
local_target=""
local_qemu=""
review_args=()

while (($#)); do
    case "$1" in
        --workspace) [[ $# -ge 2 ]] || { echo "error: --workspace needs a value" >&2; exit 2; }; workspace="$2"; shift 2 ;;
        --source-platform) [[ $# -ge 2 ]] || { echo "error: --source-platform needs a value" >&2; exit 2; }; source_platform="$2"; shift 2 ;;
        --target-platform) [[ $# -ge 2 ]] || { echo "error: --target-platform needs a value" >&2; exit 2; }; target_platform="$2"; shift 2 ;;
        --driver-name) [[ $# -ge 2 ]] || { echo "error: --driver-name needs a value" >&2; exit 2; }; driver_name="$2"; shift 2 ;;
        --catalog) [[ $# -ge 2 ]] || { echo "error: --catalog needs a value" >&2; exit 2; }; catalogs+=("$2"); shift 2 ;;
        --baseline-repository) [[ $# -ge 2 ]] || { echo "error: --baseline-repository needs a value" >&2; exit 2; }; baselines+=("$2"); shift 2 ;;
        --local-source-repository) [[ $# -ge 2 ]] || { echo "error: --local-source-repository needs a value" >&2; exit 2; }; local_source="$2"; shift 2 ;;
        --local-target-repository) [[ $# -ge 2 ]] || { echo "error: --local-target-repository needs a value" >&2; exit 2; }; local_target="$2"; shift 2 ;;
        --local-qemu-repository) [[ $# -ge 2 ]] || { echo "error: --local-qemu-repository needs a value" >&2; exit 2; }; local_qemu="$2"; shift 2 ;;
        --skill-root) [[ $# -ge 2 ]] || { echo "error: --skill-root needs a value" >&2; exit 2; }; skill_root="$2"; shift 2 ;;
        --model) [[ $# -ge 2 ]] || { echo "error: --model needs a value" >&2; exit 2; }; model="$2"; shift 2 ;;
        --context-policy) [[ $# -ge 2 ]] || { echo "error: --context-policy needs a value" >&2; exit 2; }; context_policy="$2"; shift 2 ;;
        --codex-bin) [[ $# -ge 2 ]] || { echo "error: --codex-bin needs a value" >&2; exit 2; }; codex_bin="$2"; shift 2 ;;
        --backend) [[ $# -ge 2 ]] || { echo "error: --backend needs a value" >&2; exit 2; }; backend="$2"; shift 2 ;;
        --analysis-review|--no-analysis-review) review_args+=("$1"); shift ;;
        --final-evidence-review|--no-final-evidence-review) review_args+=("$1"); shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ -n "$workspace" ]] || { echo "error: --workspace is required" >&2; exit 2; }
[[ -n "$driver_name" ]] || { echo "error: --driver-name is required" >&2; exit 2; }

for path in "${catalogs[@]}"; do
    [[ -f "$path" ]] || { echo "error: catalog is not a file: $path" >&2; exit 2; }
done
for path in "${baselines[@]}" "$local_source" "$local_target" "$local_qemu"; do
    [[ -z "$path" || -e "$path" ]] || { echo "error: repository path does not exist: $path" >&2; exit 2; }
done
if [[ -n "$skill_root" && ! -d "$skill_root" ]]; then
    echo "error: Skill root is not a directory: $skill_root" >&2
    exit 2
fi

args=(
    -m driver_port_factory.cli port run "$workspace"
    --source-platform "$source_platform"
    --target-platform "$target_platform"
    --driver-name "$driver_name"
    --backend "$backend"
    --codex-bin "$codex_bin"
)
[[ -n "$model" ]] && args+=(--model "$model")
[[ -n "$context_policy" ]] && args+=(--context-policy "$context_policy")
[[ -n "$skill_root" ]] && args+=(--skill-root "$skill_root")
for path in "${catalogs[@]}"; do args+=(--catalog "$path"); done
for path in "${baselines[@]}"; do args+=(--baseline-repository "$path"); done
[[ -n "$local_source" ]] && args+=(--local-source-repository "$local_source")
[[ -n "$local_target" ]] && args+=(--local-target-repository "$local_target")
[[ -n "$local_qemu" ]] && args+=(--local-qemu-repository "$local_qemu")
args+=("${review_args[@]}")

# This variable is commonly left behind by a previous restricted Codex run.
# It makes a new run loop on network reconnect even when repositories are local.
unset CODEX_SANDBOX_NETWORK_DISABLED
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" "${args[@]}"
