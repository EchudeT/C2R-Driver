"""Create reviewable launch commands; never invoke a model during preparation."""

import shlex
import sys
from pathlib import Path

from common import HERE, git, read, write


def script(path, argv, *, environment=None, stdin=None):
    lines = ["#!/bin/sh", "set -eu"]
    for name, value in (environment or {}).items():
        lines.append("export " + name + "=" + shlex.quote(str(value)))
    lines.append(
        "exec "
        + shlex.join(list(map(str, argv)))
        + ' "$@"'
        + (" < " + shlex.quote(str(stdin)) if stdin else "")
    )
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)


def prepare_launch(root, name, method, trial, qemu, factory=None):
    task = read(root / "seeds" / name / "task.json")
    qemu = qemu.resolve()
    git(qemu, "cat-file", "-e", task["environment"]["qemu_evidence_revision"] + "^{commit}")
    repo = HERE.parents[1]
    entry = repo / "scripts/native-experiment.py"
    if method == "dpf":
        factory = Path(factory or repo).resolve()
        if not (factory / "scripts/run-experiment.sh").is_file():
            raise ValueError("DPF trial requires --factory /path/to/driver-port-factory")
        pins = {
            "repositories": [
                {
                    "role": "source",
                    "url": str(root / "seeds" / name / "source"),
                    "ref": task["source_root_commit"],
                },
                {
                    "role": "target",
                    "url": str(root / "seeds" / name / "target"),
                    "ref": task["target_root_commit"],
                },
                {
                    "role": "qemu",
                    "url": str(qemu),
                    "ref": task["environment"]["qemu_evidence_revision"],
                },
            ]
        }
        write(trial / "repository-pins.json", pins)
        script(
            trial / "launch.sh",
            [
                factory / "scripts/run-experiment.sh",
                "--workspace",
                trial / "run",
                "--source-platform",
                "linux",
                "--target-platform",
                "asterinas",
                "--driver-name",
                name,
                "--catalog",
                repo / "configs/drivers" / f"linux-{name}.catalog.json",
                "--platform-image",
                task["environment"]["image_id"],
                "--platform-accelerator",
                "kvm",
                "--local-source-repository",
                root / "seeds" / name / "source",
                "--local-target-repository",
                root / "seeds" / name / "target",
                "--local-qemu-repository",
                qemu,
            ],
            environment={
                "DPF_NATIVE_SUITE": root,
                "DPF_REPOSITORY_PINS": trial / "repository-pins.json",
            },
        )
    else:
        for action in ["build", "check"]:
            script(
                trial / (action + ".sh"),
                [
                    sys.executable,
                    entry,
                    action,
                    "--root",
                    root,
                    "--driver",
                    name,
                    "--method",
                    method,
                    "--trial",
                    trial.name,
                    "--target",
                    trial / "target",
                ],
            )
        (trial / "PROMPT.md").write_text(
            "Translate the supplied Linux C driver into the hollow Asterinas target in Rust.\n\n"
            + task["scope"]
            + "\n\n"
            "Read source/ and target/ on demand. Only this task's removed driver and necessary "
            "integration are implementation scope. Retain other drivers and platform frameworks. "
            "Do not obtain or consult the withheld upstream Rust implementation, other task seeds, "
            "operator controls, other candidates or previous translation results.\n\n"
            "Use ./build.sh for the fixed Docker build and ./check.sh for the complete unchanged "
            "upstream test set. Tests are provided; do not write replacement assertions or edit "
            "the test sources, wrappers, boot configuration, or shared assets. "
            "A skip is not a pass. "
            "You may adapt the real driver integration. Finish with a working candidate and report "
            "any remaining limitations honestly. These public tests are not exhaustive.\n\n"
            "QEMU source evidence (read-only; no host toolchain build is needed): "
            + str(qemu)
            + " at "
            + task["environment"]["qemu_evidence_revision"]
            + ".\n"
        )
        script(
            trial / "launch.sh",
            [
                "codex",
                "exec",
                "--cd",
                trial,
                "--skip-git-repo-check",
                "--sandbox",
                "danger-full-access",
                "-c",
                'approval_policy="never"',
                "--disable",
                "apps",
            ],
            stdin=trial / "PROMPT.md",
        )
    print("Prepared launch command (not executed):", trial / "launch.sh", flush=True)
