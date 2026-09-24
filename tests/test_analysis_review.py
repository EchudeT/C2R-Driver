"""Analysis gate routing and explicit, read-only evidence location."""
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from driver_port_factory.composition import open_project
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.core.models import StageStatus, WorkflowError
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.target_study.contracts import TargetStudyStage
from driver_port_factory.review_evidence import locate
from tests.acquisition_support import git, repository
from tests.workflow_support import ready_implementation, runner
from tests.submission_support import submit


def test_locator_reads_exact_frozen_content_and_does_not_guess(tmp_path):
    repo = repository(tmp_path, 'source', {
        'a/driver.c': 'int shared;\n/* shared */\nint other;\n',
        'b/driver.c': 'int wrong_file;\n',
        'literal[1].c': 'int literal;\n',
        'page.c': '/* page\fbreak */\nint next;\n',
    })
    commit = git('rev-parse', 'HEAD', cwd=repo)
    (repo / 'a/driver.c').write_text('uncommitted replacement\n')
    query = dict(repository=repo, revision=commit, path='a/driver.c')
    result = locate(**query, start=1, end=2)
    assert result['status'] == 'LOCATED'
    assert result['lines'][0] == {'line': 1, 'text': 'int shared;'}
    assert result['semantic_verdict'] == 'NOT_EVALUATED'
    assert locate(**query, symbol='shared')['match_count'] == 2
    assert locate(**query, symbol='shared.*')['status'] == 'NO_LITERAL_MATCH'
    assert locate(**query, start=4)['status'] == 'OUT_OF_RANGE'
    assert locate(**query, start=1, expected_sha256='0' * 64)['status'] == 'HASH_MISMATCH'
    assert locate(repository=repo, revision=commit, path='driver.c', start=1)['status'] == 'PATH_NOT_FOUND'
    assert locate(repository=repo, revision=commit, path='../a/driver.c', start=1)['status'] == 'INVALID_PATH'
    assert locate(repository=repo, revision='HEAD', path='a/driver.c', start=1)['status'] == 'INVALID_REVISION'
    assert locate(repository=repo, revision=commit, path='literal[1].c', start=1)['status'] == 'LOCATED'
    physical = locate(repository=repo, revision=commit, path='page.c', start=2)
    assert physical['total_lines'] == 2
    assert physical['lines'][0]['text'] == 'int next;'
    # Exercise the exact Python tool advertised to the reviewer.
    call = subprocess.run([sys.executable, '-m', 'driver_port_factory.review_evidence',
        '--repository', str(repo), '--revision', commit, '--path', 'a/driver.c',
        '--start', '1', '--end', '3'], capture_output=True, text=True)
    assert call.returncode == 0
    assert json.loads(call.stdout)['total_lines'] == 3
    assert (repo / 'a/driver.c').read_text() == 'uncommitted replacement\n'


@pytest.mark.parametrize('repair_target', [S.CONTRACTS, TargetStudyStage.STUDY])
def test_analysis_returns_all_findings_to_worker_before_sealing(tmp_path, repair_target):
    project = ready_implementation(tmp_path, reviewed=False)
    port = runner(project)
    calls, review_count = [], 0
    with pytest.raises(WorkflowError):
        project.start(S.DRIVER_IMPLEMENTATION)

    class ReachedImplementation(Exception):
        pass

    def implementation(_):
        assert project.stage(S.ANALYSIS_REVIEW).status is StageStatus.PASS
        raise ReachedImplementation

    port._actions[S.DRIVER_IMPLEMENTATION] = implementation

    def gateway(job):
        nonlocal review_count
        calls.append(job)
        report = job.execution_root / 'report.md'
        payload = json.loads(job.prompt.split('<job>')[1].split('</job>')[0])
        if job.stage is S.ANALYSIS_REVIEW:
            review_count += 1
            assert job.thread_id == (None if review_count == 1 else 'reviewer')
            assert job.sandbox.value == 'danger-full-access'
            assert payload['instructions']['tool_runtime']['evidence_locator'][-1] == 'driver_port_factory.review_evidence'
            if review_count == 1:
                report.write_text('F1: report.md:3, source driver.c:1: inaccurate claim.\n'
                    'F2: report.md:4, test.c:2: registration is not an assertion.\n')
                submit(project, job, report, kind='report', decision='rework',
                       repair_stage=repair_target.value)
            else:
                assert payload['reference_material']['previous_review']
                report.write_text('F1 and F2 resolved against corrected evidence.\n')
                submit(project, job, report, kind='report', decision='pass')
            return CodexResult(job.job_id, '', 'reviewer')
        if job.stage is S.TARGET_FRAMEWORK_ENABLEMENT:
            report = job.execution_root / '.dpf-output' / 'report.md'
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                'Synthetic target framework enablement; no target edits required.\n'
                'DPF_SELF_REVIEW: PASS\n'
            )
            submit(project, job, report, kind='report', decision='pass')
            return CodexResult(job.job_id, '', 'worker')
        assert job.stage in {S.CONTRACTS, TargetStudyStage.STUDY}
        assert job.thread_id in {None, 'worker'}
        assert 'analysis_review' in job.prompt
        report.write_text('Corrected both F1 and F2; retained valid evidence.\n')
        submit(project, job, report, kind='report', decision='pass')
        return CodexResult(job.job_id, '', 'worker')

    with patch('driver_port_factory.codex.cli.CodexExecGateway.run', side_effect=gateway):
        with pytest.raises(ReachedImplementation):
            port._run_project(project)
    assert review_count == 2
    assert sum(job.stage is S.CONTRACTS for job in calls) == 1
    assert sum(job.stage is TargetStudyStage.STUDY for job in calls) == (repair_target is TargetStudyStage.STUDY)
    open_project(project.root).verify_integrity()


def test_analysis_blocker_prevents_implementation(tmp_path):
    project = ready_implementation(tmp_path, reviewed=False)

    def gateway(job):
        assert job.stage is S.ANALYSIS_REVIEW
        report = job.execution_root / 'review.md'
        report.write_text('Required frozen premise needs external decision.\n')
        submit(project, job, report, kind='report', decision='blocked')
        return CodexResult(job.job_id, '', 'reviewer')

    with patch('driver_port_factory.codex.cli.CodexExecGateway.run', side_effect=gateway) as model:
        outcome = runner(project)._run_project(project)
    assert outcome.stage == S.ANALYSIS_REVIEW.value
    assert outcome.status is StageStatus.BLOCKED
    assert project.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.PENDING
    assert model.call_count == 1
