#!/usr/bin/env python3
"""Print compact per-stage Codex accounting without loading full transcripts."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if sys.version_info < (3, 11):
    # The host image may still provide Python 3.8 while DPF requires 3.11.
    # Re-exec before importing DPF so running this file directly is as safe as
    # invoking it through the repository virtual environment.
    interpreter = Path(os.environ.get("DPF_PYTHON", ROOT / ".venv/bin/python"))
    if interpreter.exists() and interpreter != Path(sys.executable):
        os.execv(str(interpreter), [str(interpreter), *sys.argv])
    raise SystemExit("DPF requires Python >= 3.11; set DPF_PYTHON to a suitable interpreter")
sys.path.insert(0, str(ROOT / "src"))

from driver_port_factory.composition import open_project  # noqa: E402
from driver_port_factory.control.statistics import cost_text  # noqa: E402
from driver_port_factory.control.statistics import project_statistics  # noqa: E402
from driver_port_factory.control.runtime import controller_status  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="show DPF stage token and cost estimates")
    parser.add_argument("workspace")
    parser.add_argument("--pricing-model")
    parser.add_argument("--pricing-tier", choices=("standard", "fast", "flex", "batch"))
    args = parser.parse_args()
    project = open_project(Path(args.workspace))
    stats = project_statistics(
        project, pricing_model=args.pricing_model, pricing_tier=args.pricing_tier
    )
    jobs_by_stage: dict[str, set[str]] = {}
    for job in stats["jobs"]:
        jobs_by_stage.setdefault(job["stage"], set()).add(job["model"] or "unknown")
    print("stage\tstate\tmodel\tcalls\tinput\tcached\toutput\tusage\tUSD~")
    for row in stats["stages"]:
        usage = row["usage"]
        status = "known"
        if row["unknown_usage_calls"]:
            status = f"unknown({row['unknown_usage_calls']})"
        if row["unpriced_calls"]:
            status += f" unpriced({row['unpriced_calls']})"
        models = ",".join(sorted(jobs_by_stage.get(row["stage"], {"-"})))
        print(
            f"{row['stage']}\t{row['execution_state']}\t{models}\t{row['codex_calls']}\t"
            f"{usage['input_tokens']:,}\t{usage['cached_input_tokens']:,}\t"
            f"{usage['output_tokens']:,}\t{status}\t{cost_text(row)}"
        )
    total = stats["totals"]
    usage = total["usage"]
    print(
        f"TOTAL\t-\t-\t{total['codex_calls']}\t{usage['input_tokens']:,}\t"
        f"{usage['cached_input_tokens']:,}\t{usage['output_tokens']:,}\t-\t{cost_text(total)}"
    )
    print(f"controller={controller_status(project)['state']}")
    print(stats["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
