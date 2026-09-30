"""Visible partial retrieval failures, without introducing an all-downloads gate."""
from .contracts import AcquisitionArtifact as A, AcquisitionStage as S


def summary(project):
    ref = project.artifact(S.EVIDENCE_CLOSURE, A.EVIDENCE_RETRIEVAL_LEDGER)
    ledger = project.load_json_artifact(S.EVIDENCE_CLOSURE, A.EVIDENCE_RETRIEVAL_LEDGER)
    failed = [attempt for attempt in ledger['attempts'] if attempt['outcome'] != 'RETRIEVED']
    return {'failed_attempts': len(failed), 'total_attempts': len(ledger['attempts']),
            'ledger': {'kind': ref.kind, 'digest': ref.digest,
                       'path': str(project.artifacts.path_for_digest(ref.digest))},
            'examples': [{key: attempt.get(key) for key in ('identifier', 'facet', 'outcome')}
                         for attempt in failed[:8]],
            'instruction': 'CONTROLLED means available material, not every locator succeeded. '
            'Inspect failures relevant to required obligations; irrelevant missing documents do not '
            'require retry. Use the full ledger for details.'}
