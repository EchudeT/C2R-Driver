"""Early compile/API probes and fixed-input differential observations.

No stage PASS, public QEMU evidence, hidden tests or semantic verdict is produced.
"""
import fcntl
import hashlib
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from ..acquisition.repository import load_repository_acquisition
from ..core.execution import CommandRunner, script_command
from ..core.models import StageStatus, WorkflowError
from ..knowledge.index import file_sha256
from .diagnostics import summarize
from .implementation import worktree_files


ACTIVE = {'target_platform_study', 'migration_contracts', 'target_framework_enablement',
          'driver_implementation', 'artifact_preparation', 'public_qemu_validation'}
MAX_COMPARE_BYTES = 1_000_000


def controlled_file(project, value):
    path = (project.root / value).resolve()
    if project.root not in path.parents or not path.is_file():
        raise WorkflowError('Probe inputs/scripts must be regular files inside the run directory')
    return path


def run(project, script, *, reference=None, inputs=None, timeout=120, contracts=(), dependencies=(), question=None):
    if not any(s.status is StageStatus.RUNNING and s.name.value in ACTIVE for s in project.stages()):
        raise WorkflowError('Early probes require an active analysis or delivery stage')
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise WorkflowError('Probe timeout must be between 1 and 3600 seconds per command')
    if question is not None and (not isinstance(question, str) or not question.strip() or len(question) > 500):
        raise WorkflowError('Probe question must contain 1..500 characters')
    if reference is not None and inputs is None:
        raise WorkflowError('Differential probes require an explicit fixed input file')
    script = controlled_file(project, script)
    reference = controlled_file(project, reference) if reference is not None else None
    inputs = controlled_file(project, inputs) if inputs is not None else None
    if inputs is not None and inputs.stat().st_size > MAX_COMPARE_BYTES:
        raise WorkflowError('Probe input exceeds 1 MB; partition into smaller fixed cases')
    acquisition = load_repository_acquisition(project)
    target = acquisition.target_worktree
    worktree = project.root / target.path
    # Share the public runner lock: concurrent probes must not mutate shared output.
    root = project.control / 'experiments'
    root.mkdir(exist_ok=True)
    with (root / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        directory = project.control / 'probes' / uuid4().hex
        directory.mkdir(parents=True)
        files = list(dict.fromkeys([p for p in (script, reference, inputs) if p is not None]
                                  + [controlled_file(project, p) for p in dependencies]))
        before = {'source': worktree_files(worktree, target.base_commit),
                  'files': {str(p): file_sha256(p) for p in files}, 'base': target.base_commit,
                  'timeout': timeout,
                  'environment': {k: os.environ.get(k) for k in ('PATH', 'CARGO_HOME', 'RUSTUP_HOME', 'LANG', 'TZ')},
                  'tools': {k: shutil.which(k) for k in ('gcc', 'rustc', 'cargo', 'docker')},
                  'extractor': file_sha256(Path(__file__))}
        # Archive exact scripts and input for inspection; execute originals so relative includes work.
        archived = {}
        for i, path in enumerate(files):
            archive = directory / f'input-{i}-{path.name}'
            shutil.copyfile(path, archive)
            archived[str(path)] = {'path': str(archive), 'sha256': file_sha256(archive)}
        from ..build_cache import environment as cache_environment
        environment = {**cache_environment(project), 'DPF_TARGET_WORKTREE': str(worktree)}
        if inputs is not None:
            frozen_input = directory / 'fixed-input'
            frozen_input.write_bytes(inputs.read_bytes())
            environment['DPF_PROBE_INPUT'] = str(frozen_input)
        results, snapshots, feedback, file_snapshots = [], [], [], []
        for name, program in [('reference', reference), ('candidate', script)]:
            if program is None:
                continue
            command = CommandRunner(directory / name).run(script_command(program), cwd=worktree,
                environment=environment, timeout_seconds=timeout)
            results.append({'role': name, 'command': asdict(command)})
            feedback.append({'role': name, **summarize(command)})
            snapshots.append(worktree_files(worktree, target.base_commit))
            file_snapshots.append({str(p): file_sha256(p) if p.is_file() else None for p in files})
            if inputs is not None:
                file_snapshots[-1][str(inputs)] = file_sha256(frozen_input) if frozen_input.is_file() else None
            if (not command.launched or command.timed_out or command.exit_code != 0
                    or snapshots[-1] != before['source'] or file_snapshots[-1] != before['files']):
                break  # No useful candidate comparison after a failed or mutating reference.
        unchanged = (all(snapshot == before['source'] for snapshot in snapshots)
                     and all(snapshot == before['files'] for snapshot in file_snapshots)
                     and all(p.is_file() and file_sha256(p) == before['files'][str(p)] for p in files)
                     and (inputs is None or file_sha256(frozen_input) == before['files'][str(inputs)]))
        ok = all(r['command']['launched'] and not r['command']['timed_out']
                 and r['command']['exit_code'] == 0 for r in results)
        comparison = None
        status = 'COMMAND_OK' if ok else 'COMMAND_FAILED'
        if not unchanged:
            status = 'INPUTS_CHANGED'
        elif reference is not None and ok:
            # Exact bytes: no arbitrary filtering that could hide semantic differences.
            paths = [Path(r['command']['stdout_path']) for r in results]
            if any(p.stat().st_size > MAX_COMPARE_BYTES for p in paths):
                status = 'COMPARISON_LIMIT'
            else:
                data = [p.read_bytes() for p in paths]
                equal = data[0] == data[1]
                offset = next((i for i, (a, b) in enumerate(zip(*data)) if a != b),
                              min(map(len, data))) if not equal else None
                comparison = {'mode': 'exact_stdout_bytes', 'equal': equal,
                              'first_difference_byte': offset, 'lengths': list(map(len, data))}
                status = 'MATCH' if equal else 'MISMATCH'
        value = {'schema': 1, 'status': status, 'contracts': list(contracts),
                 'question': question, 'execution_environment': environment,
                 'identity': before, 'archived_inputs': archived, 'commands': results, 'comparison': comparison,
                 'feedback': feedback, 'timeout_per_command': timeout,
                 'authority': 'ADVISORY_ONLY: reference validity, coverage and target applicability '
                 'require review. A match on these inputs does not certify a driver. No automatic '
                 'cache: arbitrary build tools and external dependencies are not fully tracked. '
                 'Declare adapter sources/helpers using --dependency for archival; tool paths are '
                 'navigation, not verified toolchain/image identity.'}
        receipt = directory / 'receipt.json'
        receipt.write_text(json.dumps(value, indent=2) + '\n')
        # Fixed-source repeats are visible, never silently blocked or rerouted.
        key = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
        previous = [p for p in directory.parent.glob('*/receipt.json') if p != receipt]
        repeats = 0
        for path in previous:
            old = json.loads(path.read_text())
            if hashlib.sha256(json.dumps(old['identity'], sort_keys=True).encode()).hexdigest() == key:
                repeats += 1
        return {'receipt': str(receipt), 'sha256': file_sha256(receipt), 'status': status,
                'same_input_prior_runs': repeats, 'comparison': comparison, 'feedback': feedback,
                'instruction': 'Resolve design-changing uncertainty, then continue implementation. '
                'Repeated unchanged failure calls for diagnosis; fresh repetitions remain allowed.'}


def recent(project, limit=3):
    root = project.control / 'probes'
    paths = sorted(root.glob('*/receipt.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    result, successes = [], 0
    for i, path in enumerate(paths):
        try:
            value = json.loads(path.read_text())
        except (ValueError, OSError):
            continue  # Optional recipe navigation must not turn a damaged diagnostic into rework.
        if not isinstance(value, dict) or not isinstance(value.get('status'), str) or not isinstance(value.get('contracts'), list):
            continue
        successful = value['status'] in {'MATCH', 'COMMAND_OK'}
        if i < limit or (successful and successes < 2):
            result.append({'path': str(path), 'sha256': file_sha256(path), 'status': value['status'],
                           'source': {'kind': 'early_probe_receipt', 'digest': file_sha256(path), 'path': str(path)},
                           'contracts': value['contracts'],
                           'use': 'prior recipe; recheck current applicability' if successful else 'recent diagnostic'})
            if value.get('question'):
                result[-1]['question'] = value['question']
            if successful:
                try:
                    files = value.get('identity', {}).get('files', {})
                    unchanged = bool(files) and all(Path(p).is_file() and file_sha256(Path(p)) == sha
                                                    for p, sha in files.items())
                except (OSError, TypeError, ValueError, AttributeError):
                    unchanged = False
                result[-1]['declared_files_unchanged'] = unchanged
                try:
                    recipe = {'commands': [{k: r['command'][k] for k in ('argv', 'cwd')}
                                       for r in value.get('commands', [])],
                          'environment': value.get('execution_environment', {}),
                          'timeout_per_command': value.get('timeout_per_command')}
                    valid_recipe = bool(recipe['commands']) and all(
                        isinstance(c['argv'], list) and bool(c['argv'])
                        and all(isinstance(arg, str) for arg in c['argv'])
                        and isinstance(c['cwd'], str) for c in recipe['commands'])
                    valid_recipe = valid_recipe and isinstance(recipe['environment'], dict)
                except (KeyError, TypeError, AttributeError):
                    recipe, valid_recipe = None, False
                if unchanged and valid_recipe and len(json.dumps(recipe).encode()) <= 1400:
                    result[-1]['recipe'] = recipe
                else:
                    result[-1]['recipe_omitted'] = ('declared files changed or unavailable' if not unchanged
                                                    else 'incomplete or oversized; read exact receipt')
                result[-1]['reuse_scope'] = 'Historical command recipe only. Current source, external dependencies and tool/image identity remain unchecked; no execution result is cached.'
            if successful:
                successes += 1
        if i >= limit and successes >= 2:
            break
    return result
