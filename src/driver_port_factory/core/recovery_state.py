"""Cheap repair input identities, collected only after a stage failure."""
import hashlib
import json
import os
import re
import stat
import subprocess


def observe_file(path):
    from ..knowledge.index import file_sha256
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            return {"symlink": os.readlink(path)}
        if stat.S_ISREG(mode):
            return {"sha256": file_sha256(path), "mode": stat.S_IMODE(mode)}
        return {"file_type": stat.S_IFMT(mode)}
    except OSError as error:
        return {"unreadable": type(error).__name__, "errno": error.errno}


def observe_source(worktree, base):
    """Observe malformed files too: recovery must not repeat the failed validator."""
    paths = set()
    errors = []
    for args in (("diff", "--no-renames", "--name-only", "-z", base),
                 ("ls-files", "--others", "--exclude-standard", "-z")):
        try:
            result = subprocess.run(["git", "-C", str(worktree), *args],
                                    capture_output=True, text=True, timeout=30)
            if result.returncode:
                errors.append({"operation": args[0], "exit": result.returncode,
                               "error": result.stderr.strip()})
            else:
                paths.update(filter(None, result.stdout.split("\0")))
        except (OSError, subprocess.SubprocessError) as error:
            errors.append({"operation": args[0], "error": type(error).__name__})
    return {"files": {path: observe_file(worktree / path) for path in sorted(paths)
                      if not path.startswith(".dpf-output/")}, "errors": errors}


def repair_inputs(project, stage):
    state = {}
    roots = [project.root / "work" / "stage-work" / stage.value]
    if stage.value in {"driver_implementation", "artifact_preparation", "public_qemu_validation"}:
        from ..acquisition.repository import load_repository_acquisition
        target = load_repository_acquisition(project).target_worktree
        worktree = project.root / target.path
        state["source"] = observe_source(worktree, target.base_commit)
        roots = [worktree / ".dpf-output"]
    files = {}
    for root in roots:
        candidates = [root / name for name in (
            "environment-smoke.sh", "public-qemu.sh", "check-presence.sh", "runtime-artifact")]
        if stage.value in {"target_platform_study", "migration_contracts"}:
            candidates.extend(root.rglob("*.md"))
        helpers = root / "harness"
        if helpers.is_dir():
            candidates.extend(helpers.rglob("*"))
        for path in candidates:
            files[str(path.relative_to(project.root))] = observe_file(path)
    state["execution_files"] = files
    if stage.value in {"repository_acquisition", "evidence_closure"}:
        jobs = [r for r in project.current_artifact_refs(stage=stage)
                if r.kind == "codex_job_result"]
        if jobs:
            text = project.artifacts.read(max(jobs, key=lambda r: r.ordinal)).decode()
            try:
                state["selection"] = json.loads(text)
            except ValueError:
                # Malformed prose is not evidence of a changed acquisition input.
                state["selection"] = None
    return state


def fingerprint(project, stage, payload):
    value = {"upstream": payload["inputs"], "repair": repair_inputs(project, stage)}
    # Diagnostic identity separates distinct failures; ephemeral evidence paths
    # and run hashes must not turn the same failure into apparent progress.
    value["failure"] = [re.sub(r"\b[0-9a-f]{20,64}\b", "<digest>",
                              re.sub(r"(?<!\w)/[^\s,;]+", "<path>", finding))
                        for finding in payload["findings"]]
    reset = project.control / "checker-decisions" / f"{stage.value}.resume"
    value["operator_resume"] = reset.read_text() if reset.is_file() else None
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
