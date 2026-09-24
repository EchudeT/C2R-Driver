"""Bounded implementation self-test, using the same execution boundary as public QEMU."""
import json
import uuid
from dataclasses import asdict
from pathlib import Path

from ..codex.contracts import CodexContinuation
from ..core.execution import CommandRunner, script_command
from ..knowledge.index import file_sha256


def implementation_smoke(project, worktree: Path, base: str) -> dict:
    # Local imports keep the implementation/public-runtime dependency acyclic.
    from .implementation import worktree_files
    from .public_qemu import PublicQemuService, run_public_harness

    output = worktree / ".dpf-output"
    paths = {name: output / name for name in (
        "implementation-smoke.sh", "runtime-artifact", "check-presence.sh")}
    for name, path in paths.items():
        if (not path.is_file() or path.is_symlink()
                or worktree not in path.resolve().parents):
            raise CodexContinuation(
                f"Implementation self-test requires .dpf-output/{name}. Build the current "
                "driver artifact and prepare a bounded functional smoke harness, then resubmit."
            )

    def inputs():
        return {
            "files": worktree_files(worktree, base),
            "execution": {name: file_sha256(path) for name, path in paths.items()},
            "helpers": PublicQemuService._helper_inputs(worktree),
            "target_platform": project.config.target_platform,
        }

    identity = inputs()
    root = project.control / "implementation-smoke"
    # Reports are deliberately absent from the reuse key. Keep every failed run.
    for saved in sorted(root.glob("*/receipt.json"), key=lambda p: p.stat().st_mtime_ns,
                        reverse=True):
        receipt = json.loads(saved.read_text())
        if receipt["inputs"] == identity and receipt["status"] == "PASS":
            return receipt
    attempt = root / uuid.uuid4().hex
    presence = CommandRunner(attempt / "presence").run(
        script_command(paths["check-presence.sh"]), cwd=worktree,
        environment={"DPF_RUNTIME_ARTIFACT": str(paths["runtime-artifact"]),
                     "DPF_TARGET_WORKTREE": str(worktree)}, timeout_seconds=300,
    )
    observed = None
    if presence.launched and presence.exit_code == 0 and not presence.timed_out:
        observed = run_public_harness(
            attempt_dir=attempt / "runtime", script_path=paths["implementation-smoke.sh"],
            worktree=worktree, runtime_path=paths["runtime-artifact"],
            target_platform=project.config.target_platform, timeout_seconds=300,
        )
    passed = observed is not None and observed.passed and inputs() == identity
    receipt = {
        "schema_version": 1, "status": "PASS" if passed else "FAIL",
        "scope": "implementation-functional-smoke", "inputs": identity,
        "presence": json.loads(json.dumps(asdict(presence))),
        "execution": json.loads(json.dumps(asdict(observed), default=str)) if observed else None,
        "receipt_path": str(attempt / "receipt.json"),
    }
    (attempt / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if not passed:
        raise CodexContinuation(
            f"Implementation smoke failed: {attempt / 'receipt.json'}. Inspect the commands, "
            "logs and functional assertions, repair the cause locally and resubmit. "
            "Keep the failed run. Do not submit PASS based on compilation alone."
        )
    return receipt
