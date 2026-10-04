#!/usr/bin/env python3
"""Explicit local Docker/QEMU regression, never invoked by pytest; no model calls.

This exercises execution provenance and a device-model assertion, not target boot
or translated-driver correctness. Uses an existing pinned image; never pulls.
"""

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from driver_port_factory.environment.managed_smoke import run

PROBE = r"""python3 - <<'PYTHON'
import json, subprocess
commands = '\n'.join(json.dumps({'execute': c, 'id': c}) for c in
                     ('qmp_capabilities', 'query-pci', 'quit')) + '\n'
result = subprocess.run(['qemu-system-x86_64', '-machine', 'q35', '-accel', 'kvm',
                         '-nodefaults', '-display', 'none', '-device', 'pvpanic-pci',
                         '-S', '-qmp', 'stdio'], input=commands, text=True,
                        capture_output=True, timeout=5)
print(result.stdout)
print(result.stderr)
assert result.returncode == 0
responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
pci = next(r['return'] for r in responses if r.get('id') == 'query-pci')
devices = [d for bus in pci for d in bus['devices']]
assert any(d['id']['vendor'] == 0x1b36 and d['id']['device'] == 0x0011 for d in devices)
print('DEVICE_MODEL_ASSERTION_PASS')
PYTHON
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=20)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    identity = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True
    ).strip()
    cases = [(f"short-{n:02d}", PROBE, 10, 0, True, False) for n in range(args.repetitions)]
    cases += [
        ("assertion-failure", PROBE + "exit 9\n", 10, 9, True, False),
        ("timeout", "sleep 10\n", 1, 124, False, False),
        ("help-only", "qemu-system-x86_64 --version\n", 10, 0, False, False),
        ("wrong-image", PROBE, 10, 0, False, True),
    ]
    results = []
    for name, text, timeout, code, observed, infrastructure in cases:
        workspace = root / name
        workspace.mkdir()
        probe = workspace / "probe.sh"
        probe.write_text(text)
        recipe = {
            "image": args.image,
            "image_id": identity,
            "timeout_seconds": timeout,
            "accelerator": "kvm",
        }
        if infrastructure:
            recipe["image_id"] = "sha256:" + "0" * 64
        capture = run(workspace, workspace / "attempt", recipe, probe)
        evidence = json.loads(capture.output.read_text())
        passed = (
            capture.command.exit_code == code
            and bool(evidence["observations"]) == observed
            and bool(capture.infrastructure_errors) == infrastructure
        )
        results.append(
            {
                "case": name,
                "expected_result_matched": passed,
                "command": asdict(capture.command),
                "infrastructure_errors": capture.infrastructure_errors,
                "qemu_observed": bool(evidence["observations"]),
            }
        )
        (root / "summary.json").write_text(
            json.dumps(
                {
                    "scope": "real Docker/QEMU execution-layer checks; no translated driver",
                    "image": args.image,
                    "image_id": identity,
                    "results": results,
                },
                indent=2,
            )
        )
        print(name, "PASS" if passed else "FAIL", flush=True)
    raise SystemExit(0 if all(r["expected_result_matched"] for r in results) else 1)


if __name__ == "__main__":
    main()
