"""Repair evidence remains accessible while shared-thread prompts avoid duplicate bodies."""
import copy

from driver_port_factory.codex.repair_handoff import organize_repair_context


def context():
    source = {'kind': 'codex_work_report', 'digest': 'abc', 'path': '/cas/abc'}
    return {
        'repair_task': {'source': source, 'report_excerpt': 'F1 fix route\nF2 fix DMA',
                        'complete': True, 'instruction': 'one repair rule'},
        'analysis_review': dict(source), 'previous_review': dict(source),
        'analysis_review_path': '/cas/abc',
        'previous_work_report': '/cas/other',
        'repair_state': {'status': 'PREREQUISITE_COMPLETED', 'repair_report': source},
        'repair_focus': {'latest_observation': {'exit_code': 0}, 'instruction': 'duplicate rule'},
    }


def test_one_entry_keeps_distinct_evidence_and_does_not_mutate_context():
    original = context()
    saved = copy.deepcopy(original)
    supplied = {}
    out = organize_repair_context(original, 'environment_recovery', {}, supplied)
    assert original == saved
    assert not {'analysis_review', 'previous_review', 'analysis_review_path',
                'repair_state', 'repair_focus'} & out.keys()
    assert out['previous_work_report'] == '/cas/other'
    task = out['repair_task']
    assert task['state']['status'] == 'PREREQUISITE_COMPLETED'
    assert task['observations']['latest_observation']['exit_code'] == 0
    assert 'instruction' not in task['observations']
    assert 'F2' in task['report_excerpt']


def test_repeat_dedup_but_new_stage_session_report_and_view_reinject():
    supplied = {}
    organize_repair_context(context(), 'environment_recovery', {}, supplied)
    repeated = organize_repair_context(context(), 'environment_recovery', supplied, {})
    assert 'report_excerpt' not in repeated['repair_task']
    assert repeated['repair_task']['source']['path'] == '/cas/abc'
    assert repeated['repair_task']['previously_supplied']
    for stage, known in [('target_platform_study', supplied), ('environment_recovery', {})]:
        assert 'F2' in organize_repair_context(context(), stage, known, {})['repair_task']['report_excerpt']
    changed = context()
    changed['repair_task']['report_excerpt'] += '\nF3 reset'
    assert 'F3' in organize_repair_context(changed, 'environment_recovery', supplied, {})['repair_task']['report_excerpt']
    changed = context()
    changed['repair_task']['source']['digest'] = 'changed'
    assert 'report_excerpt' in organize_repair_context(changed, 'environment_recovery', supplied, {})['repair_task']


def test_no_bound_report_keeps_existing_feedback():
    value = {'controller_feedback': 'failed', 'repair_state': {'reason': 'operator request'}}
    assert organize_repair_context(value, 'driver_implementation', {}, {}) == value
