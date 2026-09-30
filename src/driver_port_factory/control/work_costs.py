"""Stable cost buckets across task fusion; no price or causal savings inference."""
ANALYSIS = {'target_platform_study', 'migration_contracts'}
DELIVERY = {'target_framework_enablement', 'driver_implementation',
            'artifact_preparation', 'public_qemu_validation'}
REVIEWS = {'analysis_review', 'final_evidence_review'}


def category(stage, reason):
    family = ('analysis' if stage in ANALYSIS else 'delivery' if stage in DELIVERY
              else 'review' if stage in REVIEWS else 'other')
    mode = ('repair' if reason == 'repair' else 'recovery' if reason == 'recovery'
            else 'self_check' if reason == 'execution_self_check'
            else 'followup' if reason == 'review_followup'
            else 'initial' if reason in {'stage_work', 'independent_review'} else 'unknown')
    return f'{family}:{mode}'


def summarize(jobs):
    result = {}
    for job in jobs:
        key = category(job['stage'], job.get('call_reason'))
        row = result.setdefault(key, {'codex_calls': 0, 'usd': 0.0, 'codex_seconds': 0.0,
                                     'unpriced_calls': 0, 'unknown_usage_calls': 0})
        row['codex_calls'] += 1
        row['codex_seconds'] += job.get('elapsed_seconds', 0)
        quote = job.get('estimate')
        row['usd'] += quote['usd'] if quote else 0
        row['unpriced_calls'] += quote is None
        row['unknown_usage_calls'] += job.get('usage') is None
    return result
