#!/usr/bin/env python3
"""Stage already verified source trees in the operator distribution repository."""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def archive(repository, destination, revision="HEAD", paths=()):
    import tarfile

    destination.mkdir()
    process = subprocess.Popen(
        ["git", "-C", str(repository), "archive", revision, *paths], stdout=subprocess.PIPE
    )
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as stream:
            for member in stream:
                stream.extract(member, destination, filter="data")
                # The safe tar filter may normalize link text; retain upstream Git bytes.
                if member.issym():
                    link = destination / member.name
                    link.unlink()
                    link.symlink_to(member.linkname)
        if process.wait():
            raise RuntimeError("Seed export failed")
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument(
        "--linux", type=Path, required=True, help="Complete pinned Linux Git repository"
    )
    args = parser.parse_args()
    suite, repository = args.suite.resolve(), args.repository.resolve()
    config = json.loads((suite / "config.json").read_text())
    revision = config["source_revision"]
    linux = args.linux.resolve()
    (repository / "source").mkdir(exist_ok=True)
    archive(linux, repository / "source/linux", revision)
    tree = subprocess.check_output(
        ["git", "-C", str(linux), "rev-parse", revision + "^{tree}"], text=True
    ).strip()
    (repository / "source/PROVENANCE.json").write_text(
        json.dumps(
            {
                "upstream": "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git",
                "revision": revision,
                "tree": tree,
                "scope": "complete-linux-tree",
            },
            indent=2,
        )
        + "\n"
    )
    for name in ["virtio-rng", "virtio-blk", "virtio-net", "nvme-pci"]:
        seed = suite / "seeds" / name
        metadata = json.loads((seed / "task.json").read_text())
        audit = json.loads((suite / "operator" / name / "seed-audit.json").read_text())
        if audit["status"] != "PASS" or audit["root_commit"] != metadata["target_root_commit"]:
            raise ValueError("Not an audited seed: " + name)
        directory = repository / "tasks" / name
        directory.mkdir(parents=True, exist_ok=False)
        head = subprocess.check_output(
            ["git", "-C", str(seed / "target"), "rev-parse", "HEAD"], text=True
        ).strip()
        if head != metadata["target_root_commit"]:
            raise ValueError("Seed root changed: " + name + "/target")
        archive(seed / "target", directory / "target")
        (directory / "source").symlink_to("../../source/linux", target_is_directory=True)
        metadata.update(
            source_root_commit=revision, source_tree=tree, source_scope="complete-linux-tree"
        )
        (directory / "task.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (directory / ".gitignore").write_text(".runtime/\n__pycache__/\n*.py[cod]\n")
        for action in ["prepare", "build", "check"]:
            path = directory / (action + ".sh")
            path.write_text(
                '#!/bin/sh\nset -eu\nTASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                'RUNNER="$TASK_ROOT/.environment/scripts/task-runtime.py"\n'
                'if [ ! -f "$RUNNER" ]; then\n'
                '  echo "First export: python3.11 scripts/export-task.py DRIVER DESTINATION" >&2\n'
                "  exit 2\nfi\n"
                'exec "${C2R_PYTHON:-python3.11}" "$RUNNER" '
                + action
                + ' --task "$TASK_ROOT" "$@"\n'
            )
            path.chmod(0o755)
        (directory / "PROMPT.md").write_text(
            "# Pure Codex + goal task\n\n" + metadata["scope"] + "\n\n"
            "Create and pursue a goal to translate the Linux C driver in source/ into target/, "
            "integrate it with the retained Asterinas frameworks, "
            "and pass the complete public suite "
            "using ./build.sh and ./check.sh on one final version, within the operator-set budget. "
            "source/ contains the complete pinned Linux tree. Locate the driver and trace callers, "
            "subsystems and kernel implementations as needed. Source entry paths are hints, "
            "not reading boundaries. Plan, investigate, implement and repair autonomously. "
            "Do not modify assertions, "
            "boot configuration or runner code. Do not consult withheld original Rust code, "
            "other task targets or prior candidates. No DPF workflow, specialized migration skill, "
            "shared knowledge base or reviewer is supplied for this baseline. Report failure "
            "honestly; goal completion is not a substitute for actual passing test receipts.\n"
        )
        (directory / "README.md").write_text(
            "# " + name + " translation input\n\n"
            "`source/` links to the complete pinned Linux tree shared in ../../source/linux/. `target/` is the verified "
            "single-driver hollow Asterinas baseline. `task.json` records upstream revisions and "
            "the source commit/tree and audited target seed commit. These are source snapshots, not translated candidates. "
            "Linux license notices are in source/COPYING and source/LICENSES/; Asterinas notices remain in target/.\n\n"
            "From the distribution root, export only this task:\n\n```sh\n"
            "python3.11 scripts/export-task.py " + name + " /path/to/worker-task\n"
            "cd /path/to/worker-task\n./prepare.sh\n# Or reuse existing shared tools/media: "
            "./prepare.sh --reuse /path/to/verified-runtime\n```\n\n"
            "Set a goal in a goal-capable Codex session in the exported directory using PROMPT.md. "
            "`build.sh` and `check.sh` use the official Asterinas Docker image. The hollow "
            "driver-specific tests initially fail by design; "
            "implement the driver to make them pass. "
            "No launcher here automatically enables goal.\n\n"
            "Do not run the model in the all-task distribution checkout. Export has a fresh "
            "Git root and no remote, other task or original Git history. Operator-only checkout "
            "and upstream Rust sources must also be excluded by worker deployment permissions.\n"
        )
        print("Staged source-only task:", name, flush=True)


if __name__ == "__main__":
    main()
