"""Explicit worker self-review of actual experiments, inside its delivery task."""
import json
from pathlib import Path

from ..core.models import StageStatus, WorkflowError
from ..knowledge.index import file_sha256


def _job(project, job_id):
    import uuid
    writable = {'target_framework_enablement', 'driver_implementation',
                'artifact_preparation', 'public_qemu_validation'}
    if not any(s.status is StageStatus.RUNNING and s.name.value in writable for s in project.stages()):
        raise WorkflowError('Experiment acknowledgment requires an active delivery worker stage')
    try:
        if str(uuid.UUID(job_id)) != job_id:
            raise ValueError("noncanonical job id")
    except (ValueError, TypeError) as error:
        raise WorkflowError("Invalid experiment job identity") from error
    matches = list((project.control / 'codex').glob(f'*-{job_id}.metrics.json'))
    if len(matches) != 1:
        raise WorkflowError('Experiment acknowledgment requires the active job identity')
    value = json.loads(matches[0].read_text())
    if value.get('invocation_state') != 'RUNNING':
        raise WorkflowError('Experiment acknowledgment job is not running')
    return value


def record_seen(project, job_id, results):
    if not job_id:
        return
    _job(project, job_id)
    path = project.control / 'experiment-reviews' / f'{job_id}.seen.json'
    path.parent.mkdir(exist_ok=True)
    previous = json.loads(path.read_text()) if path.exists() else {}
    for item in results:
        receipt = item['observation'].trace_path.parent / 'receipt.json'
        previous[item['id']] = {'path': str(receipt), 'sha256': file_sha256(receipt),
                                'status': item['status']}
    path.write_text(json.dumps(previous) + '\n')


def acknowledge(project, job_id, report):
    job = _job(project, job_id)
    report = report.resolve()
    if project.root not in report.parents or not report.is_file() or not report.read_text().strip():
        raise WorkflowError('Write the actual experiment self-review to a project report first')
    root = project.control / 'experiment-reviews'
    seen = root / f'{job_id}.seen.json'
    if not seen.is_file():
        raise WorkflowError('This job has not requested managed experiments')
    values = json.loads(seen.read_text())
    if not values or any(v['status'] != 'PASS' for v in values.values()):
        raise WorkflowError('Repair failed experiments before acknowledging a complete passing delivery')
    for value in values.values():
        if file_sha256(Path(value['path'])) != value['sha256']:
            raise WorkflowError('Observed experiment receipt changed')
    value = {'job_id': job_id, 'stage': job['stage'], 'report_sha256': file_sha256(report),
             'experiments': list(values.values())}
    path = root / f'{job_id}.ack.json'
    path.write_text(json.dumps(value) + '\n')
    return path


def acknowledged(project, report, public):
    """Reuse a self-check only when it explicitly covers this exact execution."""
    run = public['run']
    paths = ([case['receipt'] for case in run.get('cases', [])]
             if run.get('cases') else
             [str((project.root / run['exec_trace']['path']).parent / 'receipt.json')])
    expected = {str(Path(p)): file_sha256(Path(p)) for p in paths if Path(p).is_file()}
    if len(expected) != len(paths):
        return False
    for path in (project.control / 'experiment-reviews').glob('*.ack.json'):
        ack = json.loads(path.read_text())
        if ack['report_sha256'] != file_sha256(report):
            continue
        seen = {r['path']: r['sha256'] for r in ack['experiments'] if r['status'] == 'PASS'}
        if all(seen.get(p) == sha for p, sha in expected.items()):
            return True
    return False
