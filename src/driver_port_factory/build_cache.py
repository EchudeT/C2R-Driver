"""Explicit shared ccache namespaces; reuse compiler objects, never driver verdicts."""
import hashlib
import json
import subprocess
from pathlib import Path

from .acquisition.repository import load_repository_acquisition
from .core.models import EvaluationMode, WorkflowError
from .knowledge.index import file_sha256


def binding(project, profile):
    acquisition = load_repository_acquisition(project)
    return {'target': project.config.target_platform,
            'base': acquisition.target_worktree.base_commit, 'profile': profile}


def configure(project, store, profile):
    if project.config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE:
        raise WorkflowError('Cross-project build caches are only enabled for developer evidence')
    profile = Path(profile).resolve()
    if project.root not in profile.parents or not profile.is_file():
        raise WorkflowError('Build cache profile must be an explicit project JSON file')
    data = json.loads(profile.read_text())
    required = {'architecture', 'toolchain', 'configuration'}
    if not isinstance(data, dict) or not required <= data.keys() or any(not data[k] for k in required):
        raise WorkflowError('Cache profile requires architecture, toolchain and configuration identities')
    bound = binding(project, data)
    key = hashlib.sha256(json.dumps(bound, sort_keys=True).encode()).hexdigest()
    namespace = Path(store).resolve() / key
    namespace.mkdir(parents=True, exist_ok=True)
    # This config overrides permissive environment/user ccache settings for this namespace.
    config = namespace / 'ccache.conf'
    text = 'compiler_check = content\nsloppiness =\nhash_dir = true\nmax_size = 5G\n'
    if config.exists() and config.read_text() != text:
        raise WorkflowError('Shared ccache namespace has an unexpected configuration')
    config.write_text(text)
    record = {'binding': bound, 'namespace': str(namespace), 'config_sha256': file_sha256(config)}
    (project.control / 'build-cache.json').write_text(json.dumps(record) + '\n')
    return {'namespace': str(namespace), 'kind': 'ccache compiler objects',
            'instruction': 'Use ccache gcc/clang or existing ccache compiler wrappers. '
            'No Cargo target/runtime/test verdict reuse; actual hit rates depend on compiler inputs.'}


def environment(project):
    path = project.control / 'build-cache.json'
    if not path.is_file():
        return {}
    if project.config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE:
        return {}
    try:
        value = json.loads(path.read_text())
        valid = (isinstance(value, dict) and isinstance(value.get('binding'), dict)
                 and 'profile' in value['binding'] and isinstance(value.get('namespace'), str)
                 and isinstance(value.get('config_sha256'), str))
    except (ValueError, OSError):
        return {}
    if not valid:
        return {}
    if value['binding'] != binding(project, value['binding']['profile']):
        return {}
    config = Path(value['namespace']) / 'ccache.conf'
    if not config.is_file() or file_sha256(config) != value['config_sha256']:
        return {}  # Optional acceleration must not block or misroute driver translation.
    acquisition = load_repository_acquisition(project)
    return {'CCACHE_DIR': value['namespace'], 'CCACHE_CONFIGPATH': str(config),
            'CCACHE_BASEDIR': str(project.root / acquisition.target_worktree.path),
            'CCACHE_COMPILERCHECK': 'content', 'CCACHE_SLOPPINESS': ''}


def status(project):
    env = environment(project)
    if not env:
        return {'enabled': False, 'configured': (project.control / 'build-cache.json').is_file(),
                'instruction': 'No valid applicable cache environment; continue normal compilation.'}
    import os
    try:
        result = subprocess.run(['ccache', '--print-stats'], env={**os.environ, **env},
                                capture_output=True, text=True, timeout=10)
        counters = {k: int(v) for k, v in (line.split() for line in result.stdout.splitlines())}
        return {'enabled': True, 'environment': env, 'counters': counters,
                'exit_code': result.returncode, 'scope': 'shared namespace totals, not per-run savings'}
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {'enabled': True, 'environment': env, 'stats_unavailable': str(error)}


def register_commands(commands):
    from .cli_support import command_registry
    root = commands.add_parser('build-cache', help='opt-in shared ccache; no runtime/verdict reuse')
    subs = command_registry(root, dest='cache_action')
    config = subs.add_parser('configure')
    config.add_argument('path')
    config.add_argument('--store', required=True)
    config.add_argument('--profile', required=True)
    config.set_defaults(handler=command_cache)
    info = subs.add_parser('status')
    info.add_argument('path')
    info.set_defaults(handler=command_cache)


def command_cache(args):
    from .composition import open_project
    project = open_project(Path(args.path), read_only=True, verify_artifacts=False)
    value = (configure(project, args.store, args.profile) if args.cache_action == 'configure'
             else status(project))
    print(json.dumps(value, indent=2))
