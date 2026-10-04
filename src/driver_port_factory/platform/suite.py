"""Generated delivery entrypoints and explicit case registration; no device oracle defaults."""

import json
import re
import shlex
import signal
import sys
from pathlib import Path

from ..core.execution import CommandRunner, script_command
from ..core.models import WorkflowError
from ..migration.experiments import cases
from . import service
from .guest import validate_case

ENTRIES = ("implementation-smoke.sh", "public-qemu.sh")
CASE_HEADER = "#!/bin/sh\n# DPF independent device case\nset -eu\nexec "


def entry_text(worktree):
    command = [sys.executable, "-m", "driver_port_factory.platform.suite", str(worktree)]
    return "#!/bin/sh\n# DPF managed case suite\nset -eu\nexec " + shlex.join(command) + "\n"


def install(worktree):
    output = worktree / ".dpf-output"
    output.mkdir(parents=True, exist_ok=True)
    for name in ENTRIES:
        path = output / name
        if not path.exists():
            path.write_text(entry_text(worktree))
            path.chmod(0o755)


def generated(worktree, script):
    return (
        script == worktree / ".dpf-output/implementation-smoke.sh"
        and script.is_file()
        and script.read_text() == entry_text(worktree)
    )


def specification(worktree, case):
    if isinstance(case, str):
        path = (worktree / case).resolve()
        if not path.is_relative_to(worktree) or not path.is_file():
            raise WorkflowError("Case must be an existing worktree JSON file")
        case = json.loads(path.read_text())
    try:
        validate_case(case)
    except ValueError as error:
        raise WorkflowError(str(error)) from error
    return case


def register(project, name, case, contracts):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise WorkflowError("Use a short case id containing letters, digits, dash or underscore")
    if (
        not isinstance(contracts, list)
        or not contracts
        or any(not isinstance(c, str) or not c for c in contracts)
    ):
        raise WorkflowError("contracts must list the source obligation IDs checked by this case")
    from .public_tests import selected

    if selected(project):
        raise WorkflowError(
            "This run uses operator-prepared assertions. Adapt the documented "
            "driver interface; do not register additional acceptance tests."
        )
    service.active(project)
    service.verified(project)
    with service.locked(project):
        _, worktree = service.load(project)
        case = specification(worktree, case)
        registered = cases(worktree) or []
        relative = f".dpf-output/harness/cases/{name}"
        script = relative + ".sh"
        previous = next((row for row in registered if row["id"] == name), None)
        if previous and previous["script"] != script:
            raise WorkflowError("Case id belongs to a custom script; choose another id")
        directory = (worktree / relative).parent
        if not directory.resolve().is_relative_to(worktree):
            raise WorkflowError("Case directory escapes the worktree")
        command = service.command(project, "run-case") + [relative + ".json"]
        wrapper = CASE_HEADER + shlex.join(command) + "\n"
        files = {
            worktree / (relative + ".json"): json.dumps(case, indent=2) + "\n",
            worktree / script: wrapper,
        }
        for path in files:
            if path.is_symlink() or (path.exists() and previous is None):
                raise WorkflowError("Case registration refuses to overwrite an unrelated file")
        row = {
            "id": name,
            "script": script,
            "contracts": list(dict.fromkeys(contracts)),
            "timeout_seconds": case.get("timeout_seconds", 120) + 60,
        }
        updated = [row if r["id"] == name else r for r in registered]
        if previous is None:
            updated.append(row)
        manifest = worktree / ".dpf-output/experiments.json"
        if manifest.is_symlink():
            raise WorkflowError("Case manifest must be a regular worktree file")
        files[manifest] = json.dumps(updated, indent=2) + "\n"
        # Use the same bounded filesystem transaction as component scaffolding.
        from .scaffold import apply

        apply({p: p.read_text() for p in files if p.exists()}, files)
        install(worktree)
        return {
            "status": "REGISTERED_NOT_EXECUTED",
            "id": name,
            "case": relative + ".json",
            "script": script,
            "contracts": row["contracts"],
            "next": "Use driver_checks.check with cases=[this id] for development; omit cases "
            "for the full registered suite. Registration is not coverage or acceptance.",
        }


def case_inputs(worktree, script, helpers):
    """Independent generated cases bind their own spec and all common helpers.

    Other cases are execution selections, not implicit inputs to this case. Shared data belongs
    in the common harness. Explicit references to another case retain that case's files too.
    Custom scripts retain the existing conservative dependency policy.
    """
    folder = worktree / ".dpf-output/harness/cases"
    if script.parent != folder or not script.read_text().startswith(CASE_HEADER):
        return helpers
    spec = script.with_suffix(".json")
    text = spec.read_text()
    peers = {
        p.stem
        for p in folder.glob("*.sh")
        if p != script
        and p.read_text().startswith(CASE_HEADER)
        and not any(p.stem + suffix in text for suffix in (".json", ".sh"))
    }
    excluded = {
        f".dpf-output/harness/cases/{name}{suffix}" for name in peers for suffix in (".sh", ".json")
    }
    excluded.add(".dpf-output/experiments.json")
    return {name: value for name, value in helpers.items() if name not in excluded}


def run(worktree):
    """Standalone entrypoint; outer controller owns capture, identity and acceptance.

    Do not nest experiments.execute here: its outer capture already holds the experiment lock.
    """
    selected = cases(worktree)
    if not selected:
        raise WorkflowError("No registered cases; register the required device assertions first")
    paths = [(worktree / row["script"]).resolve() for row in selected]
    entries = {(worktree / ".dpf-output" / name).resolve() for name in ENTRIES}
    if any(path in entries for path in paths):
        raise WorkflowError("A suite entrypoint cannot be registered as its own case")
    failed = False
    runner = CommandRunner(worktree / ".dpf-output/qemu-runs/suite-commands")
    for row, path in zip(selected, paths, strict=True):
        print(f"Running registered case: {row['id']}", flush=True)
        result = runner.run(
            script_command(path),
            cwd=worktree,
            environment=row.get("environment", {}),
            timeout_seconds=row.get("timeout_seconds", 3600),
        )
        print(
            f"exit={result.exit_code}; timeout={result.timed_out}; "
            f"stdout={result.stdout_path}; stderr={result.stderr_path}",
            flush=True,
        )
        failed |= not result.launched or result.timed_out or result.exit_code != 0
    return 1 if failed else 0


def cancel(signum, _frame):
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, cancel)
    try:
        raise SystemExit(run(Path(sys.argv[1]).resolve()))
    except (OSError, ValueError, WorkflowError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
