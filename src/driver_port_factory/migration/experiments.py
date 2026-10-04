"""Execution observations with operator-requested persistent passing results.

Development checks retain earlier passing results without automatic reruns.
Stage 15 explicitly executes one complete fresh batch on the final candidate.
Historical per-case results never satisfy that batch; its source/artifact identity
excludes temporary paths, sessions, logs and report metadata.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from ..core.models import WorkflowError
from ..core.execution import CommandResult
from ..knowledge.index import file_sha256


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def project_for(worktree):
    from ..composition import open_project
    for root in worktree.parents:
        if (root / '.dpf/run.sqlite3').is_file():
            return open_project(root, read_only=True, verify_artifacts=False)
    return None


def _policy():
    from ..core import execution, container_trace, container_events, docker_inspection, container_policy
    from . import public_qemu
    from ..platform.profile import adapter_identity
    return adapter_identity() | {"experiments.py": file_sha256(Path(__file__))} | {Path(m.__file__).name: file_sha256(Path(m.__file__)) for m in (
        execution, container_trace, container_events, docker_inspection, container_policy, public_qemu)}


def identity(project, worktree, script, runtime, timeout, dependencies=None, environment=None):
    from ..acquisition.repository import load_repository_acquisition
    from .implementation import worktree_files
    from .public_qemu import PublicQemuService
    target = load_repository_acquisition(project).target_worktree
    helpers = PublicQemuService._helper_inputs(worktree)
    if dependencies is None:
        from ..platform.suite import case_inputs
        helpers = case_inputs(worktree, script, helpers)
    if dependencies is not None:
        selected = {}
        for name in dependencies:
            if name not in helpers:
                raise WorkflowError(f'Experiment dependency is not a regular harness input: {name}')
            selected[name] = helpers[name]
        helpers = selected
    from ..environment.contracts import EnvironmentArtifact as E, EnvironmentStage as ES
    record = project.load_json_artifact(ES.RECOVERY, E.EXPERIMENT_ROUTE)
    route = {key: record.get(key) for key in ('artifact_mode', 'command', 'device_identity', 'topology')}
    import shutil
    programs = {name: shutil.which(name) for name in ('bash', 'python3', 'docker')}
    tools = {name: file_sha256(Path(path)) for name, path in programs.items() if path and Path(path).is_file()}
    inherited = {name: os.environ.get(name) for name in ('PATH', 'CARGO_HOME', 'RUSTUP_HOME', 'LANG', 'TZ')}
    return {'tools': tools, 'inherited_environment': inherited, 'schema': 1, 'source': worktree_files(worktree, target.base_commit),
            'diagnostic_epoch': diagnostic_epoch(project),
            'base': target.base_commit, 'target': project.config.target_platform,
            'script': file_sha256(script), 'runtime': file_sha256(runtime),
            'helpers': helpers, 'timeout': timeout, 'route': route,
            'environment': environment or {}, 'execution_boundary': execution_policy(project),
            'current_images': current_images(execution_policy(project)), 'policy': _policy()}


def diagnostic_epoch(project):
    path = project.control / 'diagnostic-epoch'
    return path.read_text() if path.is_file() else None


def _evidence_paths(result):
    paths = [result.trace_path, result.trace_path.with_suffix('.collector.json'),
             result.trace_path.parent / 'container-processes.json',
             Path(result.command.stdout_path), Path(result.command.stderr_path)]
    paths.extend(Path(log['archive_path']) for log in result.logs if log.get('archive_path'))
    return paths


def _restore(value):
    from .public_qemu import QemuHarnessResult
    data = value['result']
    return QemuHarnessResult(CommandResult(**data['command']), Path(data['trace_path']),
        *(tuple(data[key]) for key in ('executed_programs', 'qemu_execs', 'host_qemu_execs',
                                      'container_qemu_execs')),
        data['container_execution'], data['runtime_bound'], tuple(data['logs']),
        tuple(data.get('case_results', [])), True)


def passed_case(project, case_id):
    """Find an existing PASS without treating later input changes as invalidation."""
    root = project.control / "experiments"
    for path in sorted(root.glob("*/receipt.json"),
                       key=lambda p: p.stat().st_mtime_ns, reverse=True):
        value = json.loads(path.read_text())
        recorded_id = value.get("case_id") or value.get("identity", {}).get("case_id")
        if recorded_id == case_id and value.get("passed"):
            return value
    return None


def execute(project, *, worktree, script_path, runtime_path, timeout_seconds=3600,
            dependencies=None, environment=None, force=False, case_id=None, final=False):
    from .public_qemu import _run_public_harness
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 86400:
        raise WorkflowError('Experiment timeout must be between 1 and 86400 seconds')
    root = project.control / 'experiments'
    root.mkdir(exist_ok=True)
    # All experiments share qemu-runs and the target tree; serialize capture.
    with (root / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        retained = passed_case(project, case_id or script_path.name)
        if retained is not None and not final:
            # Worker fresh/force does not rerun a passed development case.
            return _restore(retained)
        before = identity(project, worktree, script_path, runtime_path, timeout_seconds,
                          dependencies, environment)
        before["case_id"] = case_id or script_path.name
        key = _digest(before)
        pointer = root / f'{key}.json'
        directory = root / uuid4().hex
        result = _run_public_harness(attempt_dir=directory, script_path=script_path,
            worktree=worktree, runtime_path=runtime_path,
            target_platform=project.config.target_platform, timeout_seconds=timeout_seconds,
            environment=environment, execution_policy=execution_policy(project))
        after = identity(project, worktree, script_path, runtime_path, timeout_seconds,
                         dependencies, environment)
        after["case_id"] = case_id or script_path.name
        value = {'identity': before, 'result': json.loads(json.dumps(asdict(result), default=str)),
                 'case_id': case_id, 'passed': result.passed and before == after,
                 'programs': {p: file_sha256(Path(p)) for p in result.executed_programs
                              if Path(p).is_absolute() and Path(p).is_file()},
                 'evidence': {str(p): file_sha256(p) for p in _evidence_paths(result) if p.is_file()}}
        directory.mkdir(exist_ok=True)
        (directory / 'receipt.json').write_text(json.dumps(value, indent=2) + '\n')
        if before != after:
            raise WorkflowError(f'Experiment inputs changed while executing: {directory}')
        temporary = pointer.with_suffix(f'.tmp-{os.getpid()}')
        temporary.write_text(json.dumps(value) + '\n')
        temporary.replace(pointer)
        return result


def execution_policy(project):
    """Derive the boundary from the selected, observed route, not the platform name."""
    from ..environment.contracts import EnvironmentArtifact as E, EnvironmentStage as ES
    from ..platform.service import required, load, verified
    if required(project):
        verified(project)
        profile, _ = load(project)
        return {"required": True, "images": [profile["image"]],
                "platform_profile": _digest(profile)}
    route = project.load_json_artifact(ES.RECOVERY, E.EXPERIMENT_READY_RUN)
    # The route retains the collector's container summary nested with execution.
    def summaries(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'container_execution' and isinstance(child, dict):
                    yield child
                yield from summaries(child)
        elif isinstance(value, list):
            for child in value:
                yield from summaries(child)
    observations = list(summaries(route))
    images = sorted({image for summary in observations for image in summary.get('images', [])})
    return {'required': bool(images), 'images': images}


def cases(worktree):
    """Optional case manifest; explicit dependencies enable independent reuse."""
    from ..platform.public_tests import verify
    verify(project_for(worktree), worktree)
    path = worktree / '.dpf-output/experiments.json'
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    if not isinstance(data, list) or not data:
        raise WorkflowError('experiments.json must be a non-empty case list')
    seen = set()
    for case in data:
        if not isinstance(case, dict) or not isinstance(case.get('id'), str) or not case['id']:
            raise WorkflowError('Experiment case requires an id')
        if case['id'] in seen:
            raise WorkflowError('Experiment case ids must be unique')
        seen.add(case['id'])
        path = (worktree / case.get('script', '')).resolve()
        if worktree.resolve() not in path.parents or not path.is_file():
            raise WorkflowError(f'Experiment script missing or outside worktree: {case["id"]}')
        for key in ('contracts', 'dependencies'):
            if key in case and (not isinstance(case[key], list)
                                or any(not isinstance(x, str) for x in case[key])):
                raise WorkflowError(f'Experiment {key} must be a string list')
        timeout = case.get('timeout_seconds', 3600)
        if type(timeout) is not int or not 1 <= timeout <= 86400:
            raise WorkflowError('Experiment timeout must be between 1 and 86400 seconds')
        env = case.get('environment', {})
        if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                           for k, v in env.items()):
            raise WorkflowError('Experiment environment must map strings to strings')
    return data


def run_cases(project, worktree, runtime, *, force=False, final=False):
    """Return every independent result; failed cases do not erase successful ones."""
    result = []
    for case in cases(worktree) or []:
        observed = execute(project, worktree=worktree,
            script_path=worktree / case['script'], runtime_path=runtime,
            timeout_seconds=case.get('timeout_seconds', 3600),
            dependencies=case.get('dependencies'), environment=case.get('environment'),
            case_id=case['id'], force=force, final=final)
        result.append({'id': case['id'], 'contracts': case.get('contracts', []),
                       'status': 'PASS' if observed.passed else 'FAIL', 'observation': observed})
    return result


def run_suite(project, worktree, runtime, *, final=False):
    from dataclasses import replace
    from .implementation import worktree_files
    from ..acquisition.repository import load_repository_acquisition

    if final:
        from ..platform.public_tests import verify
        verify(project, worktree, final=True)
        base = load_repository_acquisition(project).target_worktree.base_commit
        source = worktree_files(worktree, base)
        artifact = file_sha256(runtime)
    results = run_cases(project, worktree, runtime, final=final)
    if final:
        # Version means production source and artifact bytes, never session/log/temp paths.
        receipts = [json.loads((item["observation"].trace_path.parent / "receipt.json").read_text())
                    for item in results]
        if (worktree_files(worktree, base) != source or file_sha256(runtime) != artifact
                or any(r["identity"]["source"] != source
                       or r["identity"]["runtime"] != artifact for r in receipts)):
            raise WorkflowError("Final suite candidate changed during execution; not a single-version result")
    first = results[0]["observation"]
    return replace(first,
        logs=tuple(log for item in results for log in item["observation"].logs),
        qemu_execs=tuple(line for item in results for line in item["observation"].qemu_execs),
        case_results=tuple({key: item[key] for key in ("id", "contracts", "status")} |
                           {"reused": item["observation"].reused,
                            "acceptance_policy": "final-single-version-batch" if final else "retained-development-result",
                            "receipt": str(item["observation"].trace_path.parent / "receipt.json"),
                            "receipt_sha256": file_sha256(item["observation"].trace_path.parent / "receipt.json")}
                           for item in results))


def current_images(policy):
    """A mutable image tag must not reuse execution from a different local image."""
    import subprocess
    images = policy.get('images', [])
    if not images:
        return {}
    result = {}
    for image in images:
        try:
            inspected = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image],
                                       capture_output=True, text=True, timeout=15)
            result[image] = inspected.stdout.strip() if inspected.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            result[image] = None
    return result
