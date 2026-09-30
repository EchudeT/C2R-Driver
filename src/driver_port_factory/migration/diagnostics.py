"""Bounded compiler feedback with exact provenance; no text-based repair routing."""
import json
from pathlib import Path

LIMIT = 131072


def tail(path):
    path = Path(path)
    with path.open('rb') as stream:
        size = path.stat().st_size
        stream.seek(max(0, size - LIMIT))
        text = stream.read(LIMIT).decode('utf-8', errors='replace')
    return text, size > LIMIT


def summarize(command):
    diagnostics, seen, outputs = [], set(), []
    for name in ('stdout', 'stderr'):
        path = getattr(command, f'{name}_path')
        text, truncated = tail(path)
        outputs.append({'path': path, 'sha256': getattr(command, f'{name}_sha256'),
                        'tail_truncated': truncated, 'tail': text[-1800:]})
        for line in text.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict):
                continue
            if item.get('reason') == 'compiler-message':
                item = item.get('message', {})
            if not isinstance(item, dict) or item.get('level') not in {'error', 'fatal'}:
                continue
            message = item.get('message')
            spans = item.get('spans', [])
            if not isinstance(message, str) or not isinstance(spans, list):
                continue
            primary = [{k: s.get(k) for k in ('file_name', 'line_start', 'column_start', 'label')}
                       for s in spans if isinstance(s, dict) and s.get('is_primary')][:3]
            row = {'message': message[:1000], 'locations': primary}
            identity = json.dumps(row, sort_keys=True)
            if identity not in seen:
                seen.add(identity)
                if len(diagnostics) < 8:
                    diagnostics.append(row)
    if not command.launched:
        category = 'LAUNCH_FAILURE'
    elif command.timed_out:
        category = 'TIMEOUT'
    elif command.exit_code == 0:
        category = 'COMMAND_OK'
    elif diagnostics:
        category = 'COMPILER_ERROR'
    else:
        category = 'COMMAND_FAILURE_UNCLASSIFIED'
    return {'category': category, 'exit_code': command.exit_code,
            'diagnostics': diagnostics, 'unique_diagnostics_in_tail': len(seen),
            'outputs': outputs, 'instruction': 'Use exact diagnostics and original definitions for local '
            'repair. Unknown failures require investigation; do not infer a driver defect from exit alone.'}
