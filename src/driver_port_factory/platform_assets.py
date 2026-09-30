"""Explicit, revision-bound platform facts. Imported facts never import a verdict."""
import hashlib
import json
from pathlib import Path

from .acquisition.repository import load_repository_acquisition
from .composition import open_project
from .core.models import EvaluationMode, WorkflowError
from .knowledge.index import file_sha256
from .cli_support import command_registry


def binding(project, configuration):
    if project.config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE:
        raise WorkflowError('Cross-task platform assets are disabled for blind evaluation')
    if not configuration.strip():
        raise WorkflowError('Platform configuration identity is required')
    acquisition = load_repository_acquisition(project)
    return {'target': project.config.target_platform, 'configuration': configuration,
            'repositories': {r.role.value: r.resolved_commit for r in acquisition.checkouts
                             if r.role.value in {'target', 'qemu'}}}


def export_asset(project, facts, store, configuration):
    facts = facts.resolve()
    if project.root not in facts.parents or not facts.is_file():
        raise WorkflowError('Platform facts must be a selected regular project file')
    text = facts.read_text()
    value = {'schema': 1, 'binding': binding(project, configuration), 'facts': text,
             'facts_sha256': hashlib.sha256(text.encode()).hexdigest(),
             'source_project': str(project.root),
             'authority': 'reusable-evidence-only; verify current applicability; no imported PASS'}
    data = (json.dumps(value, sort_keys=True, ensure_ascii=False) + '\n').encode()
    digest = hashlib.sha256(data).hexdigest()
    store.mkdir(parents=True, exist_ok=True)
    path = store / f'{digest}.json'
    if path.exists() and path.read_bytes() != data:
        raise WorkflowError('Platform asset content collision')
    if not path.exists():
        path.write_bytes(data)
    return path


def import_asset(project, path, configuration):
    digest = file_sha256(path)
    if path.stem != digest:
        raise WorkflowError('Platform asset filename/content identity mismatch')
    value = json.loads(path.read_text())
    if (value.get('schema') != 1 or value.get('binding') != binding(project, configuration)
            or hashlib.sha256(value['facts'].encode()).hexdigest() != value['facts_sha256']):
        raise WorkflowError('Platform asset revision/configuration/content mismatch')
    root = project.control / 'platform-assets'
    root.mkdir(exist_ok=True)
    target = root / path.name
    target.write_bytes(path.read_bytes())
    return target


def references(project):
    if project.config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE:
        return []
    result = []
    for path in sorted((project.control / 'platform-assets').glob('*.json')):
        value = json.loads(path.read_text())
        if path.stem != file_sha256(path):
            raise WorkflowError('Imported platform asset changed')
        if value['binding'] != binding(project, value['binding']['configuration']):
            continue
        result.append({'path': str(path), 'digest': path.stem, 'binding': value['binding'],
                       'instruction': 'Reuse applicable platform facts and recipes, verify against current '
                       'originals and configuration. Prior driver answers and PASS are not imported authority.'})
    return result


def command_asset(args):
    project = open_project(Path(args.path), verify_artifacts=False)
    if args.action == 'export':
        path = export_asset(project, Path(args.facts), Path(args.store), args.configuration)
        print(json.dumps({'asset': str(path), 'sha256': path.stem}))
    elif args.action == 'import':
        path = import_asset(project, Path(args.asset), args.configuration)
        print(json.dumps({'imported': str(path), 'status': 'EVIDENCE_ONLY'}))
    else:
        print(json.dumps(references(project), ensure_ascii=False, indent=2))


def register_commands(commands):
    root = commands.add_parser('platform-asset', help='explicit reusable platform evidence, never previous verdicts')
    subs = command_registry(root, dest='action')
    for action in ('export', 'import', 'status'):
        p = subs.add_parser(action)
        p.add_argument('path')
        if action != 'status':
            p.add_argument('--configuration', required=True, help='architecture/config/toolchain identity')
        if action == 'export':
            p.add_argument('--facts', required=True)
            p.add_argument('--store', required=True)
        elif action == 'import':
            p.add_argument('--asset', required=True)
        p.set_defaults(handler=command_asset)
