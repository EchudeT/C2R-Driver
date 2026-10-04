"""Cross-task evidence reuse without reusing a historical acceptance verdict."""

import json
from dataclasses import replace

import pytest

from driver_port_factory.core.models import EvaluationMode, WorkflowError
from driver_port_factory.knowledge.shared import add_experience, import_git, search, show
from driver_port_factory.knowledge.shared_capture import capture
from driver_port_factory.knowledge.shared_store import Library
from tests.acquisition_support import repository


def original(tmp_path):
    repo = repository(tmp_path, "repo", {"lock.rs": "fn try_lock() {\n return None;\n}\n"})
    library = Library(tmp_path / "library")
    # repository fixture returns a path (independent of any driver workspace).
    result = import_git(
        library,
        repo,
        "HEAD",
        ["lock.rs"],
        domain="target",
        platform="os",
        source_url="https://example.test/os",
        license_note="synthetic fixture",
    )
    return library, repo, result


def lesson(entry):
    return {
        "title": "nonblocking lock failure",
        "problem": "try_lock failed acquisition",
        "lesson": "Preserve early return",
        "conditions": "callback requires nonblocking use",
        "limitations": "source interpretation, no runtime verification",
        "tags": ["lock"],
        "evidence": [{"entry": entry, "line_start": 1, "line_end": 2}],
    }


def test_pinned_original_and_cross_driver_experience_survive_retirement(tmp_path):
    library, repo, result = original(tmp_path)
    entry = result["entries"][0]
    (repo / "lock.rs").write_text("dirty unrelated working tree")
    revision = show(library, entry)["entry"]["revision"]
    learned = add_experience(library, lesson(entry))
    packet = search(library, "nonblocking", platform="os", revision=revision)
    assert packet["results"][0]["kind"] == "EXPERIENCE"
    assert search(library, "try_lock", revision="other")["results"] == []
    assert search(library, "try_lock", revision="other", include_other_revisions=True)["results"]
    with library.writing():
        library.commit(retire=(entry, "API assumption superseded"))
    assert search(library, "nonblocking")["results"] == []
    assert search(library, "nonblocking", snapshot=learned["snapshot"])["results"]
    assert "return None" in show(library, entry, snapshot=result["snapshot"])["text"]


def test_duplicate_import_and_invalid_experience_and_corruption(tmp_path):
    library, repo, result = original(tmp_path)
    again = import_git(
        library,
        repo,
        "HEAD",
        ["lock.rs"],
        domain="target",
        platform="os",
        source_url="https://example.test/os",
        license_note="synthetic fixture",
    )
    assert again == result
    spec = lesson(result["entries"][0])
    spec["evidence"][0]["line_end"] = 1000
    with pytest.raises(WorkflowError, match="outside"):
        add_experience(library, spec)
    assert library.head() == result["snapshot"]
    value = show(library, result["entries"][0])
    (library.root / "objects" / value["entry"]["blob"]).write_bytes(b"tampered")
    with pytest.raises(WorkflowError, match="changed"):
        search(library, "try_lock")


def test_public_capture_and_snapshot_binding_do_not_depend_on_source_workspace(
    tmp_path, monkeypatch
):
    from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
    from driver_port_factory.knowledge.shared_cli import query_project
    from tests.knowledge_support import prepare_project
    from tests.migration_support import public_run

    experiment = tmp_path / "experiment"
    experiment.mkdir()
    project, _, _ = public_run(experiment, exit_code=1)
    library = Library(tmp_path / "shared")
    captured = capture(library, project)
    packet = search(library, "execution_status", platform="example-target")
    assert any(row["kind"] == "OBSERVATION" for row in packet["results"])
    for key in captured["entries"]:
        row = show(library, key)
        if row["entry"]["kind"] == "OBSERVATION":
            assert row["entry"]["attachments"]
            assert "FAIL" in row["text"]
    second = tmp_path / "second"
    second.mkdir()
    another, _ = prepare_project(second)
    monkeypatch.setenv("DPF_SHARED_KB", str(library.root))
    KnowledgeBootstrapper().build_infrastructure(another)
    assert query_project(another, "execution_status")["snapshot"] == captured["snapshot"]
    with library.writing():
        library.commit(retire=(captured["entries"][0], "withdrawn after newer evidence"))
    assert query_project(another, "execution_status")["snapshot"] == captured["snapshot"]
    from types import SimpleNamespace

    blind = SimpleNamespace(
        config=replace(project.config, evaluation_mode=EvaluationMode.PROSPECTIVE_BLIND)
    )
    with pytest.raises(WorkflowError, match="developer public"):
        capture(library, blind)
    assert json.loads(library.read(captured["snapshot"]))["type"] == "library_snapshot"
