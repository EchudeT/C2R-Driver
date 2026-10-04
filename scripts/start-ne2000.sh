#!/usr/bin/env bash
# Start/resume the prepared NE2000 experiment with its frozen controller.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
EXPERIMENT="$(cd -- "$ROOT/../experiments/ne2k-pci-luna-medium-20261003-01" && pwd -P)"
if [[ "${1:-}" == "--help" ]]; then
    echo "Usage: scripts/start-ne2000.sh [--check]"
    echo "Default: start/resume Luna medium translation in the foreground; logs remain in the experiment."
    echo "--check: inspect prepared environment without calling a model or rebuilding."
    exit 0
fi
if (($# > 1)) || [[ $# == 1 && "$1" != "--check" ]]; then
    echo "Usage: scripts/start-ne2000.sh [--check]" >&2
    exit 2
fi
"$EXPERIMENT/runtime/bin/python" - "$EXPERIMENT" <<'PY'
import json
import os
import sys
from pathlib import Path
root = Path(sys.argv[1])
value = json.loads((root / "preparation.json").read_text())
if value.get("baseline_status") != "PASS":
    raise SystemExit("NE2000 baseline preparation is incomplete; inspect preparation.log")
if not os.environ.get("CCH_API_KEY"):
    auth = root / "model-home/auth.json"
    if not auth.exists() or not json.loads(auth.read_text()).get("OPENAI_API_KEY"):
        raise SystemExit("Configured provider credential unavailable: set CCH_API_KEY before launch")
print("NE2000: baseline PASS; Docker/KVM/OVMF; gpt-5.6-luna / medium; 3 fixed public tests.")
print("Workspace:", root / "run")
PY
if [[ "${1:-}" == "--check" ]]; then
    exit 0
fi
cd -- "$EXPERIMENT"
exec ./start.sh
