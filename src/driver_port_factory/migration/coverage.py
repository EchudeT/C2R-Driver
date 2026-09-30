"""Advisory obligation coverage; never infer requirements from Markdown keywords."""
import json


def coverage(worktree, results=()):
    path = worktree / '.dpf-output/obligations.json'
    if not path.is_file():
        return {'status': 'REPORT_BASED', 'instruction':
                'Use current contracts to review every required obligation, including unexecuted ones.'}
    try:
        rows = json.loads(path.read_text())
        if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('id'), str)
                                             for r in rows):
            raise ValueError('Expected a list of obligation objects with id')
        ids = [r['id'] for r in rows]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate obligation IDs')
    except (ValueError, OSError) as error:
        return {'status': 'UNREADABLE', 'path': str(path), 'diagnostic': str(error),
                'instruction': 'Consult the authoritative contracts; malformed optional navigation is not a driver defect.'}
    mapped = {}
    for result in results:
        for obligation in result.get('contracts', []):
            mapped.setdefault(obligation, []).append({'test': result['id'], 'status': result['status']})
    return {'status': 'MAPPED', 'path': str(path), 'obligations': [
        {**row, 'observations': mapped.get(row['id'], []),
         'execution': ('FAIL' if any(x['status'] == 'FAIL' for x in mapped.get(row['id'], []))
                       else 'OBSERVED' if mapped.get(row['id']) else 'NOT_RUN')}
        for row in rows], 'instruction':
        'OBSERVED is not semantic certification. Compare every required contract with its actual oracle; '
        'do not silently mark missing behavior N/A. This optional map never changes frozen scope.'}
