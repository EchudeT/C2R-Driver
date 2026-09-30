"""Local Git-backed experiment inputs, not a clone of live controller/session state."""
from contextlib import contextmanager, suppress
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time
import uuid

from .core.models import WorkflowError, utc_now

MAX_NEW_FILE = 2 * 1024 * 1024
IDENTITY = {'GIT_AUTHOR_NAME': 'DPF checkpoint', 'GIT_AUTHOR_EMAIL': 'checkpoint@localhost',
            'GIT_COMMITTER_NAME': 'DPF checkpoint', 'GIT_COMMITTER_EMAIL': 'checkpoint@localhost'}


def git(root, *args, data=None, env=None):
    environment = dict(os.environ)
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
                'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
        environment.pop(key, None)
    try:
        result = subprocess.run(['git', '-C', str(root), *args], input=data,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
                                env={**environment, **IDENTITY, **(env or {})})
    except subprocess.TimeoutExpired as error:
        raise WorkflowError('checkpoint Git operation exceeded 120 seconds') from error
    if result.returncode:
        raise WorkflowError(result.stderr.decode(errors='replace').strip())
    return result.stdout


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def name(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value):
        raise WorkflowError('checkpoint name must be 1-80 letters, digits, underscore or hyphen')
    return value


def reference(path):
    return {'path': str(path.resolve()), 'sha256': digest(path), 'bytes': path.stat().st_size}


@contextmanager
def lock(path):
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkflowError('checkpoint requires an idle run and exclusive checkpoint writer') from error
        yield


def storage(project):
    return project.control / 'experiment-checkpoints'


def engine_fingerprint():
    root = Path(__file__).parent
    hashes = {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob('*'))
              if p.is_file() and p.suffix in {'.py', '.md', '.json'}}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


@contextmanager
def pin_transaction():
    records = []
    try:
        yield records
    except BaseException:
        for item in records:
            with suppress(OSError, WorkflowError):
                git(item['path'], 'update-ref', '-d', item['pin'], item['commit'])
        raise


def source_snapshot(path, checkpoint_id):
    """Keep HEAD and the user's index intact; pin objects against ordinary Git GC."""
    path = path.resolve()
    top = Path(git(path, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if top != path:
        raise WorkflowError(f'checkpoint source must be a Git root: {path}')
    # Never silently omit an unignored binary. Fix ignores or record it as external evidence.
    changed = (git(path, 'ls-files', '-z', '--modified', '--others', '--exclude-standard') +
               git(path, 'diff', '--name-only', '-z', 'HEAD'))
    changed_bytes = 0
    for raw in set(changed.split(b'\0')):
        if not raw:
            continue
        candidate = path / os.fsdecode(raw)
        if candidate.is_file() and not candidate.is_symlink():
            size = candidate.stat().st_size
            changed_bytes += size
            if size > MAX_NEW_FILE or changed_bytes > 32 * 1024 * 1024:
                raise WorkflowError(f'large changed source excluded from checkpoint policy: {candidate}')
    if any(row.startswith(b'160000 ') for row in git(path, 'ls-files', '--stage', '-z').split(b'\0')):
        raise WorkflowError('snapshot submodule repositories separately; gitlinks are not a complete source snapshot')
    head = git(path, 'rev-parse', 'HEAD').decode().strip()
    if not changed:
        tree = git(path, 'rev-parse', 'HEAD^{tree}').decode().strip()
        commit = head  # Clean baselines need no extra index, blob writes or snapshot commit.
    else:
        with tempfile.TemporaryDirectory(prefix='dpf-index-') as directory:
            env = {'GIT_INDEX_FILE': str(Path(directory) / 'index')}
            git(path, 'read-tree', head, env=env)
            git(path, 'add', '-A', '--', '.', env=env)
            tree = git(path, 'write-tree', env=env).decode().strip()
            commit = git(path, 'commit-tree', tree, '-p', head, data=b'DPF experiment input\n').decode().strip()
    ref = f'refs/dpf-checkpoints/{checkpoint_id}/{uuid.uuid4().hex}'
    git(path, 'update-ref', ref, commit)
    return {'path': str(path), 'head': head, 'tree': tree, 'commit': commit, 'pin': ref,
            'ignored_files': 'excluded; builds must be regenerated or supplied as explicit materials'}


def default_sources(project):
    from .acquisition.repository import load_repository_acquisition
    from .acquisition.contracts import AcquisitionStage
    from .core.models import StageStatus
    if project.stage(AcquisitionStage.REPOSITORY_ACQUISITION).status is not StageStatus.PASS:
        return []
    acquisition = load_repository_acquisition(project)
    return list(dict.fromkeys([project.root / r.checkout_path for r in acquisition.checkouts] +
                              [project.root / acquisition.target_worktree.path]))


def create(project, label, *, sources=None, materials=(), invocation=None, controller_locked=False):
    label = name(label)
    if not controller_locked:
        with lock(project.control / 'controller.lock'):
            return create(project, label, sources=sources, materials=materials,
                          invocation=invocation, controller_locked=True)
    with lock(project.control / 'checkpoint.lock'), pin_transaction() as records:
        root = storage(project)
        if not root.exists():
            root.mkdir(mode=0o700)
            git(root, 'init', '--bare', '--quiet')
        ref = f'refs/checkpoints/{label}'
        existing = git(root, 'for-each-ref', '--format=%(refname)', ref).strip()
        if existing:
            raise WorkflowError(f'checkpoint already exists: {label}')
        sources = default_sources(project) if sources is None else sources
        checkpoint_id = uuid.uuid4().hex
        for path in dict.fromkeys(Path(p).resolve() for p in sources):
            if project.root not in path.parents:
                raise WorkflowError('source must be a repository inside the project')
            records.append(source_snapshot(path, checkpoint_id))
        artifacts = {}
        for item in project.artifact_refs():
            artifacts[item.digest] = {'path': str(project.artifacts.path_for_digest(item.digest)),
                                      'sha256': item.digest, 'bytes': item.size}
        # Keep immutable native log objects shared. Freeze their small manifests in Git.
        logs = sorted((project.control / 'codex/context-logs').glob('*.json'))
        log_objects = {}
        for path in logs:
            value = json.loads(path.read_text())
            chunks = [value['prompt']] if value.get('prompt') else []
            chunks += [c for s in value.get('snapshots', []) for c in s['chunks']]
            for chunk in chunks:
                sha = chunk['sha256']
                if not re.fullmatch(r'[0-9a-f]{64}', sha):
                    raise WorkflowError('invalid context object identity')
                log_objects[sha] = {'path': str(path.parent / 'objects' / sha),
                                    'sha256': sha, 'bytes': chunk['bytes']}
        manifest = {'schema_version': 1, 'id': checkpoint_id, 'label': label, 'created_at': utc_now(),
                    'project': str(project.root), 'sources': records,
                    'engine_sha256': engine_fingerprint(),
                    'git_version': git(root, '--version').decode().strip(),
                    'artifacts': list(artifacts.values()), 'log_objects': list(log_objects.values()),
                    'mode': 'fresh-task', 'native_session_fork': False,
                    'invocation': invocation,
                    'limits': ['Not a relocatable controller backup or a native session fork.',
                               'External immutable objects and source Git repositories must be retained.',
                               'Ignored builds, running containers and provider cache are not captured.']}
        with tempfile.TemporaryDirectory(prefix='dpf-checkpoint-') as directory:
            stage = Path(directory)
            with sqlite3.connect(f'{project.database_path.as_uri()}?mode=ro', uri=True) as src:
                with sqlite3.connect(stage / 'run.sqlite3') as dest:
                    src.backup(dest)
            (stage / 'project.json').write_bytes(project.config_path.read_bytes())
            for index, path in enumerate(logs):
                (stage / f'context-{index}.json').write_bytes(path.read_bytes())
            manifest['materials'] = []
            for index, raw in enumerate(materials):
                path = Path(raw).resolve()
                if project.root not in path.parents or not path.is_file():
                    raise WorkflowError('material must be a file inside the project')
                if path.stat().st_size > MAX_NEW_FILE:
                    raise WorkflowError('large mutable materials must be frozen in CAS before checkpointing')
                destination = f'material-{index}-{path.name}'
                (stage / destination).write_bytes(path.read_bytes())
                manifest['materials'].append({**reference(stage / destination), 'path': str(path),
                                               'stored_as': destination})
            (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
            env = {'GIT_INDEX_FILE': str(stage / 'git-index')}
            git(root, 'read-tree', '--empty', env=env)
            for path in sorted(stage.iterdir()):
                if path.name == 'git-index':
                    continue
                oid = git(root, 'hash-object', '-w', '--stdin', data=path.read_bytes()).decode().strip()
                git(root, 'update-index', '--add', '--cacheinfo', f'100644,{oid},{path.name}', env=env)
            tree = git(root, 'write-tree', env=env).decode().strip()
            commit = git(root, 'commit-tree', tree, data=f'{label}\n'.encode()).decode().strip()
            git(root, 'update-ref', ref, commit, '0' * len(commit))
        return {'label': label, 'commit': commit, 'source_count': len(records),
                'shared_artifacts': len(artifacts), 'shared_log_objects': len(log_objects),
                'mode': 'fresh-task'}


def load(project, label):
    return json.loads(git(storage(project), 'show', f'refs/checkpoints/{name(label)}:manifest.json'))


def capture_invocation(project, stage, metrics, prompt):
    """Opt-in at worker boundaries. Diagnostics must never become acceptance gates."""
    if os.environ.get('DPF_EXPERIMENT_CHECKPOINTS') != '1':
        return
    if stage not in {'target_platform_study', 'migration_contracts',
                     'target_framework_enablement', 'driver_implementation'}:
        return
    started = time.monotonic()
    try:
        metrics['experiment_checkpoint'] = create(
            project, f"call-{metrics['job_id']}", controller_locked=True,
            invocation={key: metrics.get(key) for key in (
                'job_id', 'stage', 'model', 'provider', 'reasoning_effort', 'service_tier',
                'auto_compact_token_limit',
                'context_policy', 'context_action', 'policy_sha256', 'call_reason')} |
            {'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
             'thread_id_for_audit_only': metrics.get('thread_id')})
    except Exception as error:
        # Even malformed diagnostic metadata must not interrupt a paid worker task.
        # Interrupts/SystemExit deliberately remain outside this non-critical boundary.
        metrics['experiment_checkpoint'] = {'status': 'unavailable', 'error': str(error)}
    metrics['experiment_checkpoint']['capture_seconds'] = round(time.monotonic() - started, 3)


def verify(project, label):
    value = load(project, label)
    for item in value['artifacts'] + value['log_objects']:
        path = Path(item['path'])
        if not path.is_file() or path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
            raise WorkflowError(f'checkpoint shared object missing or changed: {path}')
    for item in value['sources']:
        tree = git(item['path'], 'rev-parse', f"{item['commit']}^{{tree}}").decode().strip()
        if tree != item['tree']:
            raise WorkflowError('checkpoint source tree mismatch')
        git(item['path'], 'fsck', '--connectivity-only', '--no-dangling', item['commit'])
    return value


def fork(project, label, destination, *, source_indices=None):
    """Fresh-task experiment workspace. No inherited thread, PASS or mutable CAS alias."""
    value = verify(project, label)
    if source_indices is None:
        source_indices = [len(value['sources']) - 1] if value['sources'] else []
    if any(i < 0 or i >= len(value['sources']) for i in source_indices):
        raise WorkflowError('source index outside checkpoint')
    destination = Path(destination).absolute()
    if destination.exists():
        raise WorkflowError('experiment destination must not exist')
    destination.mkdir(parents=True)
    try:
        for index, item in enumerate(value['sources']):
            if index not in source_indices:
                continue  # Read other frozen repositories on demand; do not duplicate checkouts.
            # Shared object database, private refs/index/worktree. Parent checkpoint pins the commit.
            git(destination, 'clone', '--quiet', '--shared', '--no-checkout', '--',
                item['path'], str(destination / f'source-{index}'))
            git(destination / f'source-{index}', '-c', 'core.hooksPath=/dev/null',
                'checkout', '--quiet', '--detach', item['commit'])
            checkout = destination / f'source-{index}'
            for row in git(checkout, 'ls-files', '--stage', '-z').split(b'\0'):
                if row.startswith(b'120000 '):
                    link = checkout / os.fsdecode(row.split(b'\t', 1)[1])
                    if not link.resolve().is_relative_to(checkout.resolve()):
                        raise WorkflowError('experiment symlink escapes its source checkout')
        material_dir = destination / 'inputs'
        material_dir.mkdir()
        for item in value['materials']:
            data = git(storage(project), 'show',
                       f"refs/checkpoints/{name(label)}:{item['stored_as']}")
            (material_dir / item['stored_as']).write_bytes(data)
        (destination / 'checkpoint.json').write_text(json.dumps(
            {**value, 'checked_out_sources': source_indices}, ensure_ascii=False, indent=2))
        (destination / 'START.md').write_text(
            '# Fresh-task experiment\n\n'
            'Use checkpoint.json for immutable input identities and source-N working trees.\n'
            'Start a NEW model conversation; do not resume any archived thread.\n'
            'Historical project paths in evidence are provenance, not writable experiment paths.\n'
            'Do not run the project controller against the original project from this arm.\n'
            'This is not a resumed full migration; define the task, model, cache policy and\n'
            'common acceptance endpoint before execution. Do not import historical PASS as a result.\n'
            'Read only relevant evidence on demand; raw history is retained for audit, not injected.\n')
    except BaseException:
        # Keep failed workspace for diagnosis; never remove a user supplied directory recursively.
        (destination / 'INCOMPLETE').write_text('Fork failed; do not use this experiment workspace.\n')
        raise
    return {'checkpoint_id': value['id'], 'destination': str(destination), 'mode': 'fresh-task',
            'note': 'No model invocation. Shared object stores must remain available.'}


def read_source(project, label, index, filename, *, start=1, column=0, budget=6000):
    """Read a bounded slice of frozen source without checking out another kernel/QEMU tree."""
    from .read_evidence import read_text
    value = load(project, label)
    if index < 0 or index >= len(value['sources']):
        raise WorkflowError('source index outside checkpoint')
    if not filename or Path(filename).is_absolute() or '..' in Path(filename).parts:
        raise WorkflowError('file must be relative to the frozen repository')
    item = value['sources'][index]
    spec = f"{item['commit']}:{filename}"
    if git(item['path'], 'cat-file', '-t', spec).strip() != b'blob':
        raise WorkflowError('select one source file')
    if int(git(item['path'], 'cat-file', '-s', spec)) > 8 * 1024 * 1024:
        raise WorkflowError('source file exceeds bounded reader limit')
    with tempfile.TemporaryDirectory(prefix='dpf-frozen-read-') as directory:
        path = Path(directory) / 'source'
        path.write_bytes(git(item['path'], 'cat-file', 'blob', spec))
        result = read_text(path, start=start, column=column, budget=budget)
    result['path'] = f"{item['path']}@{spec}"
    result['checkpoint_id'] = value['id']
    result['source_index'] = index
    return result


def command(args):
    from .composition import open_project
    project = open_project(Path(args.path), read_only=True, verify_artifacts=False)
    if args.action == 'create':
        result = create(project, args.name, sources=args.source, materials=args.material)
    elif args.action == 'pack':
        with lock(project.control / 'checkpoint.lock'):
            git(storage(project), 'repack', '-ad')
        result = {'packed': True, 'note': 'Only checkpoint metadata; source repositories unchanged.'}
    elif args.action == 'fork':
        result = fork(project, args.name, args.destination, source_indices=args.source_index)
    elif args.action == 'read':
        result = read_source(project, args.name, args.source_index, args.file,
                             start=args.start, column=args.column, budget=args.budget)
    elif args.action == 'verify':
        value = verify(project, args.name)
        result = {'id': value['id'], 'verified': True, 'mode': value['mode']}
    else:
        root = storage(project)
        result = (git(root, 'for-each-ref', '--format=%(refname:strip=2)',
                      'refs/checkpoints/').decode().splitlines() if root.exists() else [])
    print(json.dumps(result, ensure_ascii=False, indent=2))


def register_commands(commands):
    parser = commands.add_parser('checkpoint', help='Git-backed, shared-storage experiment inputs')
    actions = parser.add_subparsers(dest='action', required=True)
    for action in ('create', 'list', 'verify', 'fork', 'read', 'pack'):
        sub = actions.add_parser(action)
        sub.add_argument('path')
        if action not in {'list', 'pack'}:
            sub.add_argument('name')
        if action == 'create':
            sub.add_argument('--source', action='append', type=Path,
                             help='Git root; default: acquired source/target/QEMU and target worktree')
            sub.add_argument('--material', action='append', default=[], type=Path,
                             help='additional small input file; repeatable')
        if action == 'fork':
            sub.add_argument('destination', type=Path)
            sub.add_argument('--source-index', action='append', type=int,
                             help='repository to check out; repeatable; default only the last (target)')
        if action == 'read':
            sub.add_argument('--source-index', type=int, required=True)
            sub.add_argument('--file', required=True)
            sub.add_argument('--start', type=int, default=1)
            sub.add_argument('--column', type=int, default=0)
            sub.add_argument('--budget', type=int, default=6000)
        sub.set_defaults(handler=command)
