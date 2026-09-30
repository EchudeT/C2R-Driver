from unittest.mock import patch

from driver_port_factory.codex.context_focus import compact_delivery_brief
from driver_port_factory.codex.sessions import input_changes
from driver_port_factory.migration.decision_context import context
from driver_port_factory.migration.delivery_brief import excerpt
from driver_port_factory.migration.probes import recent, run
from tests.workflow_support import ready_implementation
from tests.test_translation_acceleration import active_project


def test_controller_delivery_context_actually_contains_accepted_route(tmp_path):
    from tests.workflow_support import runner
    project = ready_implementation(tmp_path)
    value = runner(project)._migration_context(project, ())
    route = value['accepted_decisions']['accepted_route']
    assert route['source']['digest'] == context(project)['accepted_route']['source']['digest']
    assert route.get('recipe') is not None


def test_successful_probe_supplies_exact_recipe_and_changed_script_is_not_reoffered(tmp_path):
    project, output = active_project(tmp_path)
    script = output / 'probe.sh'
    script.write_text('true\n')
    receipt = run(project, script, question='Does the selected compiler accept this interface?')
    first = recent(project)[0]
    assert first['path'] == receipt['receipt']
    assert first['declared_files_unchanged'] is True
    assert first['recipe']['commands'][0]['argv'][-1] == str(script)
    assert first['recipe']['commands'][0]['cwd']
    assert first['question'].startswith('Does the selected compiler')
    assert 'no execution result is cached' in first['reuse_scope']
    script.write_text('exit 3\n')
    changed = recent(project)[0]
    assert changed['declared_files_unchanged'] is False
    assert 'recipe' not in changed
    assert changed['status'] == 'COMMAND_OK'  # history remains accurate, not relabeled a new failure


def test_accepted_route_is_bound_and_contracts_supersede_study_decisions(tmp_path):
    project = ready_implementation(tmp_path)
    value = context(project)
    assert value['accepted_route']['source']['digest']
    assert 'open_decisions' not in value  # missing optional heading never causes rework
    original = project.artifacts.read
    def read(ref):
        if ref.kind == 'migration_contracts':
            return b'## Open decisions\nCan the selected interface preserve ownership?\n## Other\nexcluded\n'
        return original(ref)
    with patch.object(project.artifacts, 'read', side_effect=read):
        value = context(project)
    assert value['open_decisions']['text'] == 'Can the selected interface preserve ownership?\n'
    assert value['open_decisions']['source']['kind'] == 'migration_contracts'


def test_exact_optional_decision_excerpt_ignores_fenced_headings():
    report = '```\n## Open decisions\nnot a decision\n```\n## Open decisions\nreal question\n## Done\n'
    assert excerpt(report, heading='Open decisions')['text'] == 'real question\n'
    assert excerpt('## Other\ntext', heading='Open decisions') is None


def test_decisions_and_recipes_return_after_context_reset_but_not_on_every_resume():
    ref = {'kind': 'route', 'digest': 'one', 'path': '/cas/one'}
    raw = {'accepted_decisions': {'accepted_route': {'source': ref, 'recipe': {'command': ['build']}}},
           'recent_early_probes': [{'source': {**ref, 'kind': 'probe'}, 'recipe': {'commands': ['compile']},
                                   'declared_files_unchanged': True}]}
    first, known = input_changes(raw, {})
    assert 'recipe' in compact_delivery_brief(first)['accepted_decisions']['accepted_route']
    resumed, _ = input_changes(raw, known)
    compact = compact_delivery_brief(resumed)
    assert 'recipe' not in compact['accepted_decisions']['accepted_route']
    assert 'recipe' not in compact['recent_early_probes'][0]
    assert compact['recent_early_probes'][0]['declared_files_unchanged'] is True
    reset, _ = input_changes(raw, {})
    assert 'recipe' in compact_delivery_brief(reset)['recent_early_probes'][0]
    assert 'recipe' in raw['accepted_decisions']['accepted_route']


def test_invalidated_contracts_do_not_resurrect_old_study_questions(tmp_path):
    from types import SimpleNamespace
    from driver_port_factory.core.models import StageStatus
    from driver_port_factory.migration.contracts import MigrationStage as S
    project = ready_implementation(tmp_path)
    original = project.stage
    with patch.object(project, 'stage', side_effect=lambda stage:
                      SimpleNamespace(status=StageStatus.RUNNING) if stage is S.CONTRACTS else original(stage)):
        value = context(project)
    assert 'accepted_route' in value
    assert 'open_decisions' not in value


def test_long_route_is_referenced_not_truncated_into_an_executable_command(tmp_path):
    import json
    project = ready_implementation(tmp_path)
    original = project.artifacts.read
    def read(ref):
        if ref.kind == 'experiment_route':
            return json.dumps({'command': ['compiler', 'x' * 3000], 'cwd': '/work'}).encode()
        return original(ref)
    with patch.object(project.artifacts, 'read', side_effect=read):
        route = context(project)['accepted_route']
    assert 'recipe' not in route
    assert 'recipe_omitted' in route
    assert route['source']['digest']


def test_damaged_optional_probe_recipe_does_not_block_handoff(tmp_path):
    import json
    from types import SimpleNamespace
    project = SimpleNamespace(control=tmp_path)
    directory = tmp_path / 'probes' / 'damaged'
    directory.mkdir(parents=True)
    (directory / 'receipt.json').write_text(json.dumps({
        'status': 'COMMAND_OK', 'contracts': [], 'identity': None, 'commands': [None]}))
    value = recent(project)[0]
    assert value['status'] == 'COMMAND_OK'
    assert value['declared_files_unchanged'] is False
    assert 'recipe' not in value
