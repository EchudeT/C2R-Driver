"""High-cost task changes: no extra model, gate, or implicit scope reduction."""
import json
from pathlib import Path
from unittest.mock import patch

from driver_port_factory.codex.prompts import SkillPromptComposer, default_prompt_pack_path
from driver_port_factory.composition import WORKFLOW_STAGE_CATALOG
from driver_port_factory.core.models import ActorRole
from driver_port_factory.control.work_costs import summarize
from driver_port_factory.migration.contracts import MigrationStage as S, MigrationArtifact as A
from driver_port_factory.migration.delivery_brief import context, excerpt
from tests.workflow_support import ready_implementation, runner


def composer(project):
    return SkillPromptComposer(Path(project.config.skill_root), WORKFLOW_STAGE_CATALOG,
                               project.workflow.stage_values)


def test_delivery_brief_is_exact_bounded_and_never_a_completion_verdict():
    text = '# Analysis\n## Delivery essentials\n\nC-RING: wrap -> public hook -> original:42\n' \
           '```md\n## not a boundary\n```\n## Details\nlong background\n'
    value = excerpt(text)
    assert value['text'].startswith('\nC-RING')
    assert 'not a boundary' in value['text']
    assert 'long background' not in value['text']
    assert value['line_start'] == 3
    small = excerpt(text, budget=10)
    assert small['truncated'] and len(small['text']) == 10
    assert excerpt('# Different report format\nAll requirements documented normally\n') is None


def test_brief_reads_frozen_contract_bytes_not_mutable_worker_copy(tmp_path):
    project = ready_implementation(tmp_path)
    ref = project.artifact(S.CONTRACTS, A.CONTRACTS)
    assert context(project) is None  # existing reports remain accepted without new headings
    report = b'## Delivery essentials\nC1: observable completion through the actual entrypoint\n'
    with patch.object(project.artifacts, 'read', return_value=report):
        value = context(project)
    assert value['source']['digest'] == ref.digest
    assert 'not a controller coverage verdict' in value['instruction']


def test_rule_references_are_lazy_but_remain_policy_bound_across_resume(tmp_path):
    project = ready_implementation(tmp_path)
    c = composer(project)
    path = Path(project.config.skill_root) / 'knowledge-guided-driver-port/references/translation.md'
    marker = 'EXPENSIVE_UNUSED_REFERENCE_DETAILS'
    path.write_text(marker * 2000)
    first = c.render(stage=S.DRIVER_IMPLEMENTATION, actor_role=ActorRole.DEVELOPER)
    assert marker not in first.text
    assert '<skill_document path="delivery-task.md"' in first.text
    assert '<skill_document_reference path="knowledge-guided-driver-port/references/translation.md"' in first.text
    assert str(path) in first.text
    policy = first.policy_digest
    second = c.render(stage=S.DRIVER_IMPLEMENTATION, actor_role=ActorRole.DEVELOPER,
                      known_documents={d.relative_path: d.digest for d in first.documents})
    assert '<skill_document_reference path="knowledge-guided-driver-port/references/translation.md"' in second.text
    assert marker not in second.text
    path.write_text(marker + 'changed rule')
    assert c.policy_digest(S.DRIVER_IMPLEMENTATION) != policy


def test_merged_delivery_cost_is_not_reported_as_savings_from_stage_relabel():
    jobs = [{'stage': 'driver_implementation', 'call_reason': 'stage_work',
             'estimate': {'usd': 13.5}, 'usage': {}, 'elapsed_seconds': 100},
            {'stage': 'driver_implementation', 'call_reason': 'repair',
             'estimate': {'usd': 21}, 'usage': {}, 'elapsed_seconds': 200}]
    before = summarize(jobs)
    jobs[0]['stage'] = 'target_framework_enablement'
    assert summarize(jobs) == before
    assert before['delivery:repair']['usd'] == 21
    jobs.append({'stage': 'migration_contracts', 'call_reason': 'repair', 'estimate': None})
    assert summarize(jobs)['analysis:repair']['unpriced_calls'] == 1


def test_first_delivery_prompt_does_not_reintroduce_smoke_only_boundary(tmp_path):
    project = ready_implementation(tmp_path, reviewed=False)
    c = composer(project)
    rendered = c.render(stage=S.TARGET_FRAMEWORK_ENABLEMENT, actor_role=ActorRole.DEVELOPER)
    assert 'Do not freeze a smoke-only implementation' in rendered.text
    assert 'No cosmetic formatting' not in rendered.text  # analysis instructions not duplicated
    manifest = json.loads((default_prompt_pack_path() / 'manifest.json').read_text())
    assert manifest['stages']['migration_contracts']['on_demand_documents']
    # The configured review gate survives prompt wording changes.
    assert S.FINAL_EVIDENCE_REVIEW.value in project.workflow.stage_values
    assert runner(project) is not None


def test_delivery_excerpt_is_not_repeated_but_returns_after_new_context_or_revision():
    from driver_port_factory.codex.context_focus import compact_delivery_brief
    from driver_port_factory.codex.sessions import input_changes
    original = {'delivery_essentials': {'text': 'required hook and expected observation',
        'source': {'kind': 'migration_contracts', 'digest': 'one', 'path': '/cas/one'}}}
    first, known = input_changes(original, {})
    assert 'text' in compact_delivery_brief(first)['delivery_essentials']
    repeated, _ = input_changes(original, known)
    compact = compact_delivery_brief(repeated)
    assert 'text' not in compact['delivery_essentials']
    assert compact['delivery_essentials']['source'] == original['delivery_essentials']['source']
    original['delivery_essentials']['source']['digest'] = 'two'
    changed, _ = input_changes(original, known)
    assert 'text' in compact_delivery_brief(changed)['delivery_essentials']
