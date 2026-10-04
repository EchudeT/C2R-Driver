"""Offline controller integration with synthetic sources/execution; no model or real driver."""

import json
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from driver_port_factory.core.models import EvaluationMode
from driver_port_factory.knowledge import learning
from driver_port_factory.knowledge.learn_tool import run, tool
from driver_port_factory.knowledge.shared import search
from driver_port_factory.knowledge.shared_binding import freeze
from driver_port_factory.knowledge.shared_cli import query_project
from driver_port_factory.knowledge.shared_store import Library
from driver_port_factory.migration.public_qemu import PublicQemuService
from tests.knowledge_support import prepare_project
from tests.migration_support import public_run

FINDING = {
    "lesson": "Reuse the source driver callback behavior",
    "conditions": "Synthetic source only; inspect current target definitions",
    "sources": [
        {"repository": "source", "path": "drivers/example.c", "line_start": 1, "line_end": 3}
    ],
}


def test_acceptance_publishes_to_common_library_and_next_task_reads_it(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-10-03T10:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-03T10:00:00+00:00")
    monkeypatch.setenv("DPF_SHARED_KB", str(tmp_path / "read-snapshot"))
    monkeypatch.setenv("DPF_SHARED_KB_PUBLISH", str(tmp_path / "common"))
    first = tmp_path / "first"
    first.mkdir()
    project, _, report = public_run(first, self_check=False)
    before = Library(tmp_path / "read-snapshot").head()
    assert (
        learning.remember_optional(project, "synthetic-job", {"lessons": [FINDING]})["lessons"] == 1
    )
    assert learning.publish_optional(project)["status"] == "NOT_ELIGIBLE"
    PublicQemuService().accept_self_review(project, work_report_path=report)
    library = Library(tmp_path / "common")
    packet = search(library, "callback", kind="EXPERIENCE")
    assert len(packet["results"]) == 1
    assert Library(tmp_path / "read-snapshot").head() == before
    assert query_project(project, "callback")["results"] == []
    head = library.head()
    assert learning.publish_optional(project)["status"] == "PUBLISHED"
    assert library.head() == head

    second = tmp_path / "second"
    second.mkdir()
    monkeypatch.setenv("DPF_SHARED_KB", str(library.root))
    monkeypatch.delenv("DPF_SHARED_KB_PUBLISH")
    another, _ = prepare_project(second)
    freeze(another)
    assert query_project(another, "callback", kind="EXPERIENCE")["results"]
    from driver_port_factory.knowledge.reuse import context

    # Query uses a mechanism, not the originating driver's name; no driver equality filter.
    offered = context(another, "target_platform_study", "callback")
    assert offered["experiences"][0]["reference"].startswith("R")
    from driver_port_factory.knowledge.shared_cli import inspect_reference, learning_status

    inspect_reference(
        SimpleNamespace(path=another.root, reference=offered["experiences"][0]["reference"])
    )
    assert json.loads(capsys.readouterr().out)["originals"][0]["text"]
    learning_status(SimpleNamespace(path=project.root, publish=True))
    assert json.loads(capsys.readouterr().out)["status"] == "PUBLISHED"
    assert "OFFERED_NOT_ADOPTION" in str(
        learning.events(another, learning.RunEvent.KNOWLEDGE_OFFERED)
    )
    project.verify_integrity()


def test_small_tool_archives_and_bad_optional_input_does_not_block(tmp_path, monkeypatch):
    monkeypatch.setenv("DPF_SHARED_KB", str(tmp_path / "library"))
    project, _ = prepare_project(tmp_path)
    freeze(project)
    from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
    from driver_port_factory.target_study.contracts import TargetStudyStage

    KnowledgeBootstrapper().build_infrastructure(project)
    project.start(TargetStudyStage.STUDY)
    job = str(uuid.uuid4())
    root = project.control / "codex"
    root.mkdir(exist_ok=True)
    (root / f"target_platform_study-{job}.metrics.json").write_text(
        json.dumps({"stage": "target_platform_study", "invocation_state": "RUNNING"})
    )
    args = {**FINDING, "sources": "source:drivers/example.c:1-3"}
    assert json.loads(run(project, job, args))["lessons"] == 1
    assert len(tool()["inputSchema"]["properties"]) == 3
    bad = json.loads(run(project, job, {**args, "sources": "source:missing.c:1"}))
    assert bad["skipped"] and project.stage(TargetStudyStage.STUDY).status.value == "RUNNING"
    from driver_port_factory.codex.check_mcp import respond

    assert "knowledge_learn" in {
        t["name"] for t in respond(project, job, {"method": "tools/list"})["tools"]
    }


def test_library_outage_preserves_acceptance_and_can_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("DPF_SHARED_KB", str(tmp_path / "library"))
    project, _, report = public_run(tmp_path, self_check=False)
    learning.remember_optional(project, "synthetic-job", {"lessons": [FINDING]})
    with patch.object(Library, "commit", side_effect=OSError("disk unavailable")):
        PublicQemuService().accept_self_review(project, work_report_path=report)
    from driver_port_factory.migration.contracts import MigrationStage

    assert project.stage(MigrationStage.PUBLIC_QEMU_VALIDATION).status.value == "PASS"
    assert learning.publish_optional(project)["status"] == "PUBLISHED"
    blind = SimpleNamespace(
        config=replace(project.config, evaluation_mode=EvaluationMode.PROSPECTIVE_BLIND)
    )
    assert (
        learning.remember_optional(blind, "job", {"lessons": [FINDING]})["status"]
        == "NOT_REQUESTED_OR_CONFIGURED"
    )
