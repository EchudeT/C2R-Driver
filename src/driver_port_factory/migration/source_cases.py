"""Budgeted public input generation from explicitly cited source requirements.

No model call, private assertion, automatic candidate-derived oracle or stage gate.
"""
import hashlib
import json
from pathlib import Path

from ..core.models import WorkflowError
from ..knowledge.index import KnowledgeIndex, file_sha256


def generate(project, spec_path, *, budget=12):
    if type(budget) is not int or not 1 <= budget <= 64:
        raise WorkflowError('Public case budget must be between 1 and 64')
    spec_path = Path(spec_path).resolve()
    if project.root not in spec_path.parents or not spec_path.is_file():
        raise WorkflowError('Source test specification must be a project file')
    spec = json.loads(spec_path.read_text())
    fields = spec.get('integer_fields', {})
    if not isinstance(fields, dict) or not 1 <= len(fields) <= 8:
        raise WorkflowError('Specify 1..8 integer fields; choose one behavior instead of a whole-driver matrix')
    evidence = spec.get('evidence', [])
    if not isinstance(evidence, list) or not evidence:
        raise WorkflowError('Source-derived cases need original source/spec/test citations')
    records = KnowledgeIndex.for_project(project).verified_records()
    allowed = {str((project.root / r['path']).resolve()): r for r in records
               if r['domain'] in {'source', 'hardware', 'test'}}
    citations = []
    for item in evidence:
        path = (project.root / item['path']).resolve()
        record = allowed.get(str(path))
        start, end = item.get('line_start'), item.get('line_end')
        if (not record or type(start) is not int or type(end) is not int
                or not 1 <= start <= end <= len(path.read_text().splitlines())):
            raise WorkflowError('Case citations must reference lines of controlled original materials')
        citations.append({'path': str(path), 'sha256': record['sha256'], 'line_start': start, 'line_end': end})
    baseline, values = {}, {}
    for name, bounds in fields.items():
        if not isinstance(name, str) or not name or not isinstance(bounds, dict):
            raise WorkflowError('Each field needs a name and integer min/max')
        lo, hi = bounds.get('min'), bounds.get('max')
        if type(lo) is not int or type(hi) is not int or lo > hi:
            raise WorkflowError('Integer field requires min <= max')
        baseline[name] = (lo + hi) // 2
        values[name] = list(dict.fromkeys([lo, hi, min(lo + 1, hi), max(hi - 1, lo)]))
    candidates = spec.get('critical_cases', [])
    if not isinstance(candidates, list):
        raise WorkflowError('critical_cases must be a list')
    candidates = list(candidates)
    if len(candidates) > budget:
        raise WorkflowError('Explicit critical cases exceed budget; choose a smaller scope or increase budget explicitly')
    for case in candidates:
        if (not isinstance(case, dict) or set(case) != set(fields)
                or any(type(v) is not int for v in case.values())):
            raise WorkflowError('Critical cases must specify exactly the integer fields')
    candidates.append(baseline)
    # Round-robin single-factor boundaries, not Cartesian products.
    for i in range(4):
        for name, options in values.items():
            if i < len(options):
                candidates.append({**baseline, name: options[i]})
    unique = list({json.dumps(v, sort_keys=True): v for v in candidates}.values())
    cases = unique[:budget]
    value = {'schema': 1, 'cases': cases, 'evidence': citations,
             'contract': spec.get('contract'), 'spec_sha256': file_sha256(spec_path),
             'generator_sha256': file_sha256(Path(__file__)),
             'budget': budget, 'omitted_generated_cases': max(0, len(unique) - budget),
             'limits': 'PUBLIC_DEVELOPER_INPUTS_ONLY. Numeric ranges and critical cases are authored '
             'claims, not semantically proved by citations. No candidate source is read by this '
             'generator; author/context independence is not established. No expected outputs or '
             'automatic test execution. No pairwise/exhaustive coverage. Use the original C adapter '
             'or a reviewed specification oracle; do not label this blind evaluation.'}
    data = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    root = project.control / 'source-cases'
    root.mkdir(exist_ok=True)
    path = root / (hashlib.sha256(data).hexdigest() + '.json')
    path.write_bytes(data)
    return {'path': str(path), 'sha256': file_sha256(path), 'case_count': len(cases),
            'omitted_generated_cases': value['omitted_generated_cases'],
            'instruction': 'Optional public vectors under cases. Run only if they resolve an actual '
            'obligation; adapters read this JSON via DPF_PROBE_INPUT. No new model review required.'}
