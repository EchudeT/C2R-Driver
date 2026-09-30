"""On-demand Clang translation-unit queries. Not a complete program analysis."""
import hashlib
import json
import shlex
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from ..core.execution import CommandRunner
from ..core.models import WorkflowError
from .index import file_sha256


def compile_entry(database, source):
    entries = json.loads(database.read_text())
    matches = []
    for entry in entries:
        directory = Path(entry['directory'])
        if not directory.is_absolute():
            directory = database.parent / directory
        if (directory / entry['file']).resolve() == source:
            matches.append((entry, directory.resolve()))
    if len(matches) != 1:
        raise WorkflowError('Select a compilation database containing exactly one configuration for this file')
    entry, directory = matches[0]
    args = entry.get('arguments') or shlex.split(entry['command'])
    if not args or Path(args[0]).name not in {'cc', 'gcc', 'clang', 'c++', 'g++', 'clang++'}:
        raise WorkflowError('Semantic query needs a direct C/C++ compile command, without shell or launcher wrappers')
    cleaned, skip = [], False
    for arg in args[1:]:
        if skip:
            skip = False
            continue
        if arg in {'-o', '-MF', '-MT', '-MQ', '-MJ'}:
            skip = True
        elif arg in {'-c', '-S', '-E', '-M', '-MM', '-MD', '-MMD', '-MP', '-MG'}:
            continue
        elif arg.startswith(('-o', '-MF', '-MT', '-MQ', '-MJ')):
            continue
        elif arg.startswith('@') or arg in {'&&', ';', '|'}:
            raise WorkflowError('Expand response files and shell commands before semantic query')
        elif (directory / arg).resolve() != source:
            cleaned.append(arg)
    return directory, cleaned


def dependencies(path):
    text = path.read_text().replace('\\\n', ' ')
    return shlex.split(text.split(':', 1)[1])


def extract(ast, source):
    rows = []
    texts = {}
    last_file = str(source)
    def location(loc, inherited):
        nonlocal last_file
        loc = loc.get('expansionLoc', loc)
        file = loc.get('file', last_file if loc else inherited)
        last_file = file
        line = loc.get('line')
        if line is None and 'offset' in loc and Path(file).is_file():
            if file not in texts:
                texts[file] = Path(file).read_bytes()
            line = texts[file][:loc['offset']].count(b'\n') + 1
        return file, line
    def visit(node, inherited):
        file, line = location(node.get('loc') or node.get('range', {}).get('begin', {}), inherited)
        kind = node.get('kind', '')
        if kind in {'FunctionDecl', 'RecordDecl', 'FieldDecl', 'EnumDecl', 'EnumConstantDecl',
                    'TypedefDecl', 'VarDecl', 'DeclRefExpr', 'MemberExpr'}:
            referenced = node.get('referencedDecl', {})
            name = node.get('name') or referenced.get('name')
            if name and line:
                rows.append({'name': name, 'kind': kind, 'file': file, 'line': line,
                             'type': (node.get('type', {}).get('qualType') or '')[:300],
                             'referenced_kind': referenced.get('kind')})
        for child in node.get('inner', []):
            visit(child, file)
    visit(ast, str(source))
    return rows


def query(project, database, source, symbol, *, limit=8):
    if not symbol or not 1 <= limit <= 30:
        raise WorkflowError('Semantic lookup requires a symbol and limit between 1 and 30')
    database, source = Path(database).resolve(), Path(source).resolve()
    if any(project.root not in p.parents or not p.is_file() for p in (database, source)):
        raise WorkflowError('Compilation database and source must be project files')
    cwd, flags = compile_entry(database, source)
    if cwd != project.root and project.root not in cwd.parents:
        raise WorkflowError('Compilation directory must be inside the run')
    compiler = shutil.which('clang++' if source.suffix in {'.cc', '.cpp', '.cxx'} else 'clang')
    if not compiler:
        return {'status': 'UNAVAILABLE', 'reason': 'Clang not installed; use source navigation'}
    try:
        version = subprocess.run([compiler, '--version'], capture_output=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return {'status': 'UNAVAILABLE', 'reason': 'Cannot identify the configured Clang compiler'}
    identity = {'compiler_version': hashlib.sha256(version).hexdigest(), 'source': str(source), 'cwd': str(cwd), 'flags': flags,
                'database': file_sha256(database), 'compiler': file_sha256(Path(compiler)),
                'policy': file_sha256(Path(__file__))}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    root = project.control / 'semantic'
    root.mkdir(exist_ok=True)
    pointer = root / f'{key}.json'
    directory = root / uuid4().hex
    directory.mkdir()
    dep = directory / 'headers.d'
    # Re-resolve includes on every query: a newly introduced shadow header must invalidate reuse.
    preflight = CommandRunner(directory / 'preprocess').run(
        [compiler, *flags, '-E', '-MD', '-MF', str(dep), str(source)], cwd=cwd, timeout_seconds=120)
    if preflight.exit_code != 0 or preflight.timed_out or not dep.is_file():
        from ..migration.diagnostics import summarize
        return {'status': 'UNAVAILABLE', 'feedback': summarize(preflight),
                'instruction': 'Cannot index this configuration; use original-source navigation.'}
    deps = {(cwd / p).resolve() for p in dependencies(dep)} | {source}
    hashes = {str(p): file_sha256(p) for p in deps}
    preprocessed_hash = preflight.stdout_sha256
    value = None
    if pointer.is_file():
        old = json.loads(pointer.read_text())
        if old['dependencies'] == hashes and old.get('preprocessed_sha256') == preprocessed_hash:
            index = Path(old['index'])
            if index.is_file() and file_sha256(index) == old['index_sha256']:
                value = json.loads(index.read_text())
    if value is None:
        command = CommandRunner(directory).run([compiler, *flags, '-fsyntax-only', '-Xclang',
            '-ast-dump=json', '-MD', '-MF', str(dep), str(source)], cwd=cwd, timeout_seconds=120)
        if command.exit_code != 0 or command.timed_out or not dep.is_file():
            from ..migration.diagnostics import summarize
            return {'status': 'UNAVAILABLE', 'feedback': summarize(command),
                    'instruction': 'This indexing failure is not a driver defect; use original-source lookup.'}
        ast = Path(command.stdout_path)
        if ast.stat().st_size > 64_000_000:
            return {'status': 'SIZE_LIMIT', 'path': str(ast), 'instruction': 'Select a smaller translation unit.'}
        deps = {(cwd / p).resolve() for p in dependencies(dep)} | {source}
        after = {str(p): file_sha256(p) for p in deps}
        if after != hashes:
            return {'status': 'INPUTS_CHANGED', 'instruction': 'Retry lookup after concurrent edits finish.'}
        value = extract(json.loads(ast.read_text()), source)
        index = directory / 'index.json'
        index.write_text(json.dumps(value) + '\n')
        record = {'identity': identity, 'dependencies': hashes, 'preprocessed_sha256': preprocessed_hash, 'index': str(index),
                  'index_sha256': file_sha256(index), 'compiler_output': str(ast)}
        temp = root / f'{uuid4().hex}.tmp'
        temp.write_text(json.dumps(record) + '\n')
        temp.replace(pointer)
    matches = [row for row in value if row['name'] == symbol]
    return {'status': 'INDEXED', 'symbol': symbol, 'matches': matches[:limit],
            'total_matches': len(matches), 'index_receipt': str(pointer),
            'limits': 'One C/C++ translation unit/configuration parsed by Clang. No Rust semantic index, '
            'record-layout sizes, whole-program call graph, alias/lock proof or hardware semantics. '
            'Exact-symbol lookup; absence is not proof. Confirm GCC-specific behavior with the actual build.'}
