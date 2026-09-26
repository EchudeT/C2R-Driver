"""Bounded implementation self-test, using the same execution boundary as public QEMU."""
import json
import uuid
from dataclasses import asdict
from pathlib import Path

from ..codex.contracts import CodexContinuation
from ..core.execution import CommandRunner, script_command
from ..knowledge.index import file_sha256
from .implementation_preflight import format_findings, inspect_implementation


def smoke_policy_digest() -> str:
    """Invalidate reuse when the collector or acceptance rules actually change."""
    import hashlib

    from ..core import container_policy, container_trace
    from . import implementation_preflight, public_qemu
    modules = (Path(__file__), Path(implementation_preflight.__file__),
               Path(public_qemu.__file__), Path(container_trace.__file__),
               Path(container_policy.__file__))
    return hashlib.sha256("".join(file_sha256(p) for p in modules).encode()).hexdigest()


def failure_observation(presence, observed, *, unchanged: bool) -> dict:
    """Facts only: do not infer a driver bug from a missing collector observation."""
    value = {"presence_exit": presence.exit_code, "presence_timeout": presence.timed_out,
             "presence_launched": presence.launched, "inputs_unchanged": unchanged}
    if observed is not None:
        value.update(harness_exit=observed.command.exit_code,
                     harness_timeout=observed.command.timed_out,
                     qemu_observed=bool(observed.qemu_execs),
                     container_observed=observed.container_execution["satisfied"],
                     runtime_bound=observed.runtime_bound, logs_observed=bool(observed.logs))
    return value


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
            "validation_policy": smoke_policy_digest(),
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
    preflight = inspect_implementation(project, worktree, base)
    if preflight["status"] != "PASS":
        # Persist the deterministic finding before returning to the worker.  A
        # missing runtime premise must be visible in status/audit and must not
        # consume a QEMU invocation merely to rediscover a text marker.
        receipt = {
            "schema_version": 2,
            "status": "FAIL",
            "scope": "implementation-functional-smoke",
            "inputs": identity,
            "preflight": preflight,
            "presence": None,
            "execution": None,
            "receipt_path": str(attempt / "receipt.json"),
        }
        attempt.mkdir(parents=True, exist_ok=True)
        (attempt / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        raise CodexContinuation(
            f"{format_findings(preflight)} Receipt: {attempt / 'receipt.json'}",
            receipt=str(attempt / "receipt.json"),
            observation={"preflight_errors": [f["code"] for f in preflight["findings"]
                                              if f.get("severity") == "error"]},
        )
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
    unchanged = inputs() == identity
    passed = observed is not None and observed.passed and unchanged
    observation = failure_observation(presence, observed, unchanged=unchanged)
    receipt = {
        "schema_version": 2, "status": "PASS" if passed else "FAIL",
        "scope": "implementation-functional-smoke", "inputs": identity,
        "preflight": preflight,
        "presence": json.loads(json.dumps(asdict(presence))),
        "execution": json.loads(json.dumps(asdict(observed), default=str)) if observed else None,
        "receipt_path": str(attempt / "receipt.json"),
        "observation": observation,
        "acceptance_scope": "execution-and-worker-oracle; not independent functional certification",
    }
    (attempt / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if not passed:
        raise CodexContinuation(
            f"Implementation smoke failed: {attempt / 'receipt.json'}. "
            f"Observed: {json.dumps(observation, sort_keys=True)}. Inspect the commands, "
            "logs and functional assertions, repair the cause locally and resubmit. "
            "Keep the failed run. Do not submit PASS based on compilation alone.",
            receipt=str(attempt / "receipt.json"), observation=observation,
        )
    return receipt
