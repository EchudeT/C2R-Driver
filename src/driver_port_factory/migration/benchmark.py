"""Operator-configured benchmark acceptance, independent of model review.

The adapter owns stimuli and assertions. The controller freezes its declared
inputs, runs it, and verifies complete results and candidate identity. This is
not isolation against malicious same-user processes or dependency discovery.
"""

import json
import fcntl
import re
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path

from ..core.execution import CommandRunner
from ..core.models import FileArtifact, StageStatus, WorkflowError
from ..knowledge.index import file_sha256
from .contracts import MigrationArtifact as A, MigrationStage as S
from .implementation import validate_worktree_snapshot

MAX_RESULT = 4 * 1024 * 1024


def _digest(value):
    import hashlib

    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate_spec(value):
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "id",
        "argv",
        "cwd",
        "required_cases",
        "timeout_seconds",
        "environment",
        "files",
        "manifest_sha256",
    }:
        raise WorkflowError("Invalid frozen benchmark specification")
    if value["schema_version"] != 1 or not isinstance(value["id"], str) or not value["id"]:
        raise WorkflowError("Benchmark requires schema_version=1 and an id")
    if not isinstance(value["manifest_sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", value["manifest_sha256"]
    ):
        raise WorkflowError("Benchmark manifest digest is invalid")
    argv, cases = value["argv"], value["required_cases"]
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(a, str) or not a or "\0" in a for a in argv)
    ):
        raise WorkflowError("Benchmark argv must be a nonempty string list")
    if (
        not isinstance(cases, list)
        or not cases
        or any(not isinstance(c, str) or not c for c in cases)
        or len(set(cases)) != len(cases)
    ):
        raise WorkflowError("Benchmark requires unique nonempty required_cases")
    if type(value["timeout_seconds"]) is not int or not 1 <= value["timeout_seconds"] <= 86400:
        raise WorkflowError("Benchmark timeout must be between 1 and 86400 seconds")
    if not isinstance(value["cwd"], str) or not Path(value["cwd"]).is_absolute():
        raise WorkflowError("Benchmark cwd must be absolute")
    env = value["environment"]
    if not isinstance(env, dict) or any(
        not isinstance(k, str)
        or not isinstance(v, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
        or k.startswith("DPF_")
        for k, v in env.items()
    ):
        raise WorkflowError(
            "Benchmark environment must contain strings and cannot override DPF_ variables"
        )
    files = value["files"]
    if (
        not isinstance(files, dict)
        or not files
        or any(
            not Path(p).is_absolute()
            or not isinstance(h, str)
            or not re.fullmatch(r"[0-9a-f]{64}", h)
            for p, h in files.items()
        )
        or argv[0] not in files
    ):
        raise WorkflowError("Benchmark must freeze its executable and declared files")


def load_spec(path):
    """Resolve a manifest once, before creating a project; never during a worker turn."""
    path = Path(path).resolve()
    raw = json.loads(path.read_text())
    required = {"schema_version", "id", "argv", "required_cases", "files"}
    if (
        not isinstance(raw, dict)
        or not required <= raw.keys()
        or raw.keys() - required - {"cwd", "timeout_seconds", "environment"}
    ):
        raise WorkflowError(
            "Benchmark manifest needs schema_version, id, argv, required_cases and files"
        )
    cwd = (path.parent / raw.get("cwd", ".")).resolve()
    argv = raw["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise WorkflowError("Benchmark argv must be a string list")
    # Preserve virtualenv interpreter symlink paths; resolving them changes Python's prefix.
    executable = (
        (cwd / argv[0]).absolute()
        if "/" in argv[0]
        else Path(shutil.which(argv[0]) or "/nonexistent").absolute()
    )
    declared = raw["files"]
    if (
        not isinstance(declared, list)
        or not declared
        or not all(isinstance(p, str) for p in declared)
    ):
        raise WorkflowError(
            "Declare benchmark adapter, tests, assertions and configuration in files"
        )
    files = list(dict.fromkeys([executable, *((path.parent / p).resolve() for p in declared)]))
    if not cwd.is_dir() or any(not p.is_file() for p in files):
        raise WorkflowError("Benchmark cwd, executable or declared input is missing")
    value = {
        **raw,
        "argv": [str(executable), *argv[1:]],
        "cwd": str(cwd),
        "timeout_seconds": raw.get("timeout_seconds", 3600),
        "environment": raw.get("environment", {}),
        "files": {str(p): file_sha256(p) for p in files},
        "manifest_sha256": file_sha256(path),
    }
    validate_spec(value)
    return value


def verify_files(spec):
    validate_spec(spec)
    for name, digest in spec["files"].items():
        path = Path(name)
        if not path.is_file() or file_sha256(path) != digest:
            raise WorkflowError(f"Frozen benchmark input changed or disappeared: {name}")


def assess(result, spec, identity):
    """No success inferred from exit code, a global PASS, omitted cases or zero tests."""
    if not isinstance(result, dict) or set(result) != {
        "schema_version",
        "benchmark_id",
        "candidate_identity",
        "cases",
    }:
        raise WorkflowError("Benchmark result has invalid fields")
    if (
        result["schema_version"] != 1
        or result["benchmark_id"] != spec["id"]
        or result["candidate_identity"] != identity
    ):
        raise WorkflowError("Benchmark result is detached from this benchmark/candidate")
    rows = result["cases"]
    if not isinstance(rows, list):
        raise WorkflowError("Benchmark cases must be a list")
    seen = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "status", "assertions"}
            or not isinstance(row["id"], str)
            or row["id"] in seen
            or not isinstance(row["status"], str)
            or row["status"] not in {"PASS", "FAIL", "SKIP", "NOT_RUN", "ERROR"}
            or type(row["assertions"]) is not int
            or row["assertions"] < 0
        ):
            raise WorkflowError("Benchmark case result is malformed or duplicated")
        seen.add(row["id"])
    if seen != set(spec["required_cases"]):
        raise WorkflowError("Benchmark results must match the complete frozen case set")
    return all(row["status"] == "PASS" and row["assertions"] > 0 for row in rows)


def _candidate(project):
    bundle = project.load_json_artifact(S.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE)
    worktree = validate_worktree_snapshot(project.root, bundle)
    runtime = project.artifact(S.ARTIFACT_PREPARATION, A.RUNTIME_ARTIFACT)
    project.artifacts.read(runtime)
    artifact_identity = project.artifact(S.ARTIFACT_PREPARATION, A.ARTIFACT_IDENTITY)
    project.artifacts.read(artifact_identity)
    value = {
        "implementation": project.artifact(S.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE).digest,
        "runtime": runtime.digest,
        "artifact_identity": artifact_identity.digest,
        "benchmark": _digest(project.config.benchmark),
    }
    return worktree, project.artifacts.path_for_digest(runtime.digest), value


def run(project):
    root = project.control / "experiments"
    root.mkdir(exist_ok=True)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run(project)


def _run(project):
    if project.stage(S.BENCHMARK_VALIDATION).status is StageStatus.READY:
        project.start(S.BENCHMARK_VALIDATION)
    if project.stage(S.BENCHMARK_VALIDATION).status is not StageStatus.RUNNING:
        raise WorkflowError("Benchmark stage must be running")
    spec = project.config.benchmark
    verify_files(spec)
    worktree, runtime, candidate = _candidate(project)
    identity = _digest(candidate)
    root = project.control / "benchmark" / uuid.uuid4().hex
    root.mkdir(parents=True)
    result_path = root / "result.json"
    environment = {
        **spec["environment"],
        "DPF_TARGET_WORKTREE": str(worktree),
        "DPF_RUNTIME_ARTIFACT": str(runtime),
        "DPF_BENCHMARK_RESULT": str(result_path),
        "DPF_CANDIDATE_IDENTITY": identity,
        "DPF_BENCHMARK_ID": spec["id"],
    }
    # Archive declared oracle bytes before executing, retaining every failed attempt.
    for index, name in enumerate(spec["files"]):
        shutil.copyfile(name, root / f"input-{index}")
    command = CommandRunner(root / "commands").run(
        spec["argv"],
        cwd=Path(spec["cwd"]),
        environment=environment,
        timeout_seconds=spec["timeout_seconds"],
    )
    passed, result, error = False, None, None
    try:
        verify_files(spec)
        if _candidate(project)[2] != candidate:
            raise WorkflowError("Benchmark candidate changed while executing")
        if not command.launched or command.timed_out or command.exit_code != 0:
            raise WorkflowError("Benchmark command failed or timed out; inspect original logs")
        if (
            not result_path.is_file()
            or result_path.is_symlink()
            or result_path.stat().st_size > MAX_RESULT
        ):
            raise WorkflowError("Benchmark result missing, symlinked or oversized")
        result = json.loads(result_path.read_text())
        passed = assess(result, spec, identity)
    except (WorkflowError, ValueError, OSError) as failure:
        error = str(failure)
    report = {
        "schema_version": 1,
        "status": "PASS" if passed else "FAIL",
        "candidate": candidate,
        "candidate_identity": identity,
        "spec": spec,
        "command": asdict(command),
        "result": result,
        "error": error,
        "authority": "configured benchmark assertions; no model verdict",
    }
    path = root / "receipt.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    project.record_artifact(S.BENCHMARK_VALIDATION, FileArtifact(A.BENCHMARK_ATTEMPT, path))
    if passed:
        project.finalize_stage(S.BENCHMARK_VALIDATION, (FileArtifact(A.BENCHMARK_REPORT, path),))
    else:
        project.complete(
            S.BENCHMARK_VALIDATION,
            StageStatus.FAIL,
            message=f"Benchmark did not pass: {path}. Inspect evidence before choosing a repair.",
        )
    return path


def validate_bundle(context):
    from ..core.validation import json_object

    report = json_object(context.one_current(A.BENCHMARK_REPORT)[1], "benchmark report")
    config = json.loads((context.project_root / ".dpf/project.json").read_text())
    spec = config.get("benchmark")
    verify_files(spec)
    expected = {
        "implementation": context.one_dependency(A.IMPLEMENTATION_BUNDLE)[0].digest,
        "runtime": context.one_dependency(A.RUNTIME_ARTIFACT)[0].digest,
        "artifact_identity": context.one_dependency(A.ARTIFACT_IDENTITY)[0].digest,
        "benchmark": _digest(spec),
    }
    bundle = json_object(context.one_dependency(A.IMPLEMENTATION_BUNDLE)[1], "implementation")
    validate_worktree_snapshot(context.project_root, bundle)
    if (
        report.get("status") != "PASS"
        or report.get("candidate") != expected
        or report.get("spec") != spec
        or report.get("candidate_identity") != _digest(expected)
        or not assess(report.get("result"), spec, _digest(expected))
    ):
        raise WorkflowError("Benchmark acceptance is incomplete or stale")
    command = report["command"]
    if (
        not command["launched"]
        or command["timed_out"]
        or command["exit_code"] != 0
        or command["argv"] != spec["argv"]
        or command["cwd"] != spec["cwd"]
    ):
        raise WorkflowError("Benchmark execution did not succeed")
    for stream in ("stdout", "stderr"):
        path = Path(command[f"{stream}_path"])
        if (
            not path.is_relative_to(context.project_root)
            or file_sha256(path) != command[f"{stream}_sha256"]
        ):
            raise WorkflowError("Benchmark execution evidence changed")


def verify_current(project):
    """Reopening a completed project must not return a stale benchmark PASS."""
    ref = project.artifact(S.BENCHMARK_VALIDATION, A.BENCHMARK_REPORT)
    project._validate_stage_bundle(S.BENCHMARK_VALIDATION, ((ref, project.artifacts.read(ref)),))
    _candidate(project)  # Also re-read CAS runtime bytes, not only metadata references.
