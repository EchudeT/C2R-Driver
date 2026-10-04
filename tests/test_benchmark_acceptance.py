"""Offline benchmark adapters validate orchestration, not real driver behavior."""

import json
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.core.models import StageStatus, WorkflowError
from driver_port_factory.migration import benchmark
from driver_port_factory.migration.contracts import MigrationStage as S, MigrationArtifact as A
from tests.migration_support import public_run


def adapter(tmp_path, *, status="PASS", assertions=1, omit=False):
    path = tmp_path / "adapter.py"
    path.write_text(
        "import json, os\nfrom pathlib import Path\n"
        "result = {'schema_version': 1, 'benchmark_id': os.environ['DPF_BENCHMARK_ID'], "
        "'candidate_identity': os.environ['DPF_CANDIDATE_IDENTITY'], 'cases': "
        + repr([] if omit else [{"id": "operation", "status": status, "assertions": assertions}])
        + "}\nPath(os.environ['DPF_BENCHMARK_RESULT']).write_text(json.dumps(result))\n"
    )
    manifest = tmp_path / "benchmark.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "synthetic-controller-fixture",
                "argv": [sys.executable, str(path)],
                "files": [str(path)],
                "required_cases": ["operation"],
                "timeout_seconds": 5,
            }
        )
    )
    return benchmark.load_spec(manifest)


def project_with_benchmark(tmp_path, spec):
    from driver_port_factory.composition import initialize_project

    def initialize(root, config):
        return initialize_project(
            root, replace(config, benchmark=spec, enable_final_evidence_review=False)
        )

    with patch("tests.knowledge_support.initialize_project", side_effect=initialize):
        return public_run(tmp_path)


@pytest.mark.parametrize(
    "status,assertions,omit",
    [
        ("PASS", 1, False),
        ("FAIL", 1, False),
        ("SKIP", 0, False),
        ("PASS", 0, False),
        ("PASS", 1, True),
    ],
)
def test_benchmark_controls_completion_without_final_model_review(
    tmp_path, status, assertions, omit
):
    spec = adapter(tmp_path, status=status, assertions=assertions, omit=omit)
    project, _, _ = project_with_benchmark(tmp_path, spec)
    assert S.FINAL_EVIDENCE_REVIEW.value not in project.workflow.stage_values
    assert project.stages()[-1].name is S.BENCHMARK_VALIDATION
    receipt = benchmark.run(project)
    passed = status == "PASS" and assertions > 0 and not omit
    assert project.stage(S.BENCHMARK_VALIDATION).status is (
        StageStatus.PASS if passed else StageStatus.FAIL
    )
    assert json.loads(receipt.read_text())["status"] == ("PASS" if passed else "FAIL")
    assert bool(project.current_artifact_refs(stage=S.BENCHMARK_VALIDATION))
    if passed:
        assert project.artifact(S.BENCHMARK_VALIDATION, A.BENCHMARK_REPORT)


def test_changed_oracle_cannot_be_accepted_by_worker(tmp_path):
    spec = adapter(tmp_path)
    project, _, report = project_with_benchmark(tmp_path, spec)
    Path(spec["argv"][1]).write_text("raise SystemExit(0)\n")
    with pytest.raises(WorkflowError, match="Frozen benchmark input"):
        benchmark.run(project)
    from driver_port_factory.core.checker_decision import accept_decision

    with pytest.raises(WorkflowError, match="cannot be overridden"):
        accept_decision(project, S.BENCHMARK_VALIDATION, tmp_path / "no-receipt", report)


def test_missing_duplicate_and_stale_candidate_results_do_not_pass(tmp_path):
    spec = adapter(tmp_path)
    row = {"id": "operation", "status": "PASS", "assertions": 1}
    result = {
        "schema_version": 1,
        "benchmark_id": spec["id"],
        "candidate_identity": "candidate",
        "cases": [row],
    }
    assert benchmark.assess(result, spec, "candidate")
    with pytest.raises(WorkflowError, match="detached"):
        benchmark.assess(result, spec, "new candidate")
    with pytest.raises(WorkflowError, match="duplicated"):
        benchmark.assess({**result, "cases": [row, row]}, spec, "candidate")
    with pytest.raises(WorkflowError, match="complete frozen"):
        benchmark.assess({**result, "cases": []}, spec, "candidate")
    with pytest.raises(WorkflowError, match="malformed"):
        benchmark.assess({**result, "cases": [{**row, "status": ["PASS"]}]}, spec, "candidate")


def test_completed_benchmark_cannot_be_reused_after_source_or_evidence_changes(tmp_path):
    project, worktree, _ = project_with_benchmark(tmp_path, adapter(tmp_path))
    benchmark.run(project)
    benchmark.verify_current(project)
    source = worktree / "driver.rs"
    original = source.read_bytes()
    source.write_bytes(original + b"// new candidate\n")
    with pytest.raises(WorkflowError, match="snapshot"):
        benchmark.verify_current(project)
    source.write_bytes(original)
    value = project.load_json_artifact(S.BENCHMARK_VALIDATION, A.BENCHMARK_REPORT)
    Path(value["command"]["stdout_path"]).write_text("changed evidence")
    with pytest.raises(WorkflowError, match="evidence changed"):
        benchmark.verify_current(project)


def test_both_model_reviews_can_be_disabled_with_real_benchmark_stage(tmp_path):
    from driver_port_factory.composition import initialize_project
    from driver_port_factory.migration.target_framework import TargetFrameworkEnablementService
    from tests.workflow_support import ready_implementation

    spec = adapter(tmp_path)

    def initialize(root, config):
        return initialize_project(
            root,
            replace(
                config,
                benchmark=spec,
                enable_analysis_review=False,
                enable_final_evidence_review=False,
            ),
        )

    def ready(root):
        project = ready_implementation(root, reviewed=False)
        project.start(S.TARGET_FRAMEWORK_ENABLEMENT)
        report = project.root / "framework.md"
        report.write_text("Synthetic fixture, no framework changes.\n")
        TargetFrameworkEnablementService().snapshot_worktree(project, report)
        return project

    with (
        patch("tests.knowledge_support.initialize_project", side_effect=initialize),
        patch("tests.migration_support.ready_implementation", side_effect=ready),
    ):
        project, _, _ = public_run(tmp_path)
    benchmark.run(project)
    assert project.stages()[-1].status is StageStatus.PASS
    assert all("review" not in stage.name.value for stage in project.stages())


def test_timeout_keeps_attempt_and_never_publishes_acceptance(tmp_path):
    spec = adapter(tmp_path)
    program = Path(spec["argv"][1])
    program.write_text("import time\ntime.sleep(10)\n")
    spec["files"][str(program)] = benchmark.file_sha256(program)
    spec["timeout_seconds"] = 1
    project, _, _ = project_with_benchmark(tmp_path, spec)
    receipt = json.loads(benchmark.run(project).read_text())
    assert receipt["command"]["timed_out"] and receipt["status"] == "FAIL"
    assert project.stage(S.BENCHMARK_VALIDATION).status is StageStatus.FAIL
