"""Short presentation must preserve stale-content rejection and original identities."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.read_evidence import main as read_main
from driver_port_factory.short_refs import References, compact


def registry(tmp_path):
    (tmp_path / ".dpf").mkdir()
    return References(tmp_path)


def test_append_only_scope_concurrent_and_no_hash_in_compact_view(tmp_path):
    refs = registry(tmp_path)
    value = {"path": "driver.rs", "sha256": "a" * 64, "revision": "b" * 40, "text": "fn init() {}"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(lambda _: refs.put("provenance", value), range(12)))
    assert len(set(tokens)) == 1
    view = compact(value, refs)
    assert "a" * 64 not in json.dumps(view)
    assert refs.get(view["evidence_ref"]) == value
    changed = compact({**value, "sha256": "c" * 64}, refs)
    assert changed["evidence_ref"] != view["evidence_ref"]
    other = tmp_path / "other"
    other.mkdir()
    second = registry(other)
    second.put("provenance", value)
    with pytest.raises(WorkflowError, match="different workspace"):
        second.get(tokens[0])


def test_cli_cursor_keeps_query_and_ref_rejects_changed_content(tmp_path, monkeypatch, capsys):
    refs = registry(tmp_path)
    monkeypatch.setenv("DPF_WORKER_PROJECT", str(tmp_path))
    path = tmp_path / "source.rs"
    path.write_text("needle " + "中文" * 500 + "\nskip\nneedle end\n")
    assert read_main(["--path", str(path), "--contains", "needle", "--budget", "400"]) == 0
    page = json.loads(capsys.readouterr().out)
    token = page["evidence_ref"]
    assert "sha256" not in page
    fragments = [r["text"] for r in page["excerpts"]]
    while page["next"]:
        assert read_main(["--cursor", page["next"], "--budget", "400"]) == 0
        page = json.loads(capsys.readouterr().out)
        fragments.extend(r["text"] for r in page["excerpts"])
    assert "".join(fragments) == "needle " + "中文" * 500 + "\nneedle end\n"
    original = refs.get(token)
    path.write_text("changed")
    assert read_main(["--ref", token]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "CONTENT_CHANGED"
    assert refs.get(token) == original


def test_probe_short_citation_is_expanded_and_frozen_at_submission(tmp_path, monkeypatch):
    from driver_port_factory.codex.policy import CodexExecutionPolicy
    from driver_port_factory.codex.submission import write_submission
    from driver_port_factory.knowledge.index import KnowledgeIndex
    from driver_port_factory.target_study.contracts import TargetStudyStage as S
    from tests.test_target_knowledge_quality import ready

    project, report = ready(tmp_path)
    target = CodexExecutionPolicy().grant(project, S.STUDY).execution_root
    target.mkdir(parents=True, exist_ok=True)
    moved = target / report.name
    moved.write_bytes(report.read_bytes())
    moved.with_suffix(".route.json").write_bytes(report.with_suffix(".route.json").read_bytes())
    spec = json.loads(report.with_suffix(".probes.json").read_text())
    refs = References(project.root)
    index = KnowledgeIndex.for_project(project)
    for row in spec["probes"]:
        for evidence in row["evidence"]:
            chunk = index.show(evidence.pop("chunk_id"))["result"]
            evidence["evidence"] = compact(chunk, refs)["evidence_ref"]
    moved.with_suffix(".probes.json").write_text(json.dumps(spec))
    job = str(uuid4())
    monkeypatch.setenv("DPF_WORKER_PROJECT", str(project.root))
    monkeypatch.setenv("DPF_WORKER_JOB", job)
    monkeypatch.setenv("DPF_WORKER_STAGE", S.STUDY.value)
    receipt = write_submission(
        project, S.STUDY, job_id=job, file_path=str(moved), kind="report", decision="pass"
    )
    saved = json.loads(receipt.read_text())["knowledge_probe_specification"]
    assert "chunk_id" in saved["probes"][0]["evidence"][0]
    with pytest.raises(WorkflowError, match="bound worker"):
        write_submission(
            project,
            S.STUDY,
            job_id=str(uuid4()),
            file_path=str(moved),
            kind="report",
            decision="pass",
        )
    # Only the persisted receipt is needed; mutable model references do not enter the gate.
    from driver_port_factory.target_study.service import TargetStudyService

    TargetStudyService().accept(project, moved, specification=saved)
    assert project.stage(S.STUDY).status.value == "PASS"


@pytest.mark.parametrize("thread", [None, "existing-worker"])
def test_worker_job_binding_survives_resume_and_cli_defaults(tmp_path, monkeypatch, thread):
    import subprocess
    import tomllib
    from unittest.mock import patch

    from driver_port_factory.cli import parser
    from driver_port_factory.codex.contracts import CodexSandbox
    from driver_port_factory.codex.gateway import CodexExecGateway, CodexJob
    from driver_port_factory.core.models import ActorRole
    from driver_port_factory.migration.contracts import MigrationStage as S

    job = CodexJob(
        S.DRIVER_IMPLEMENTATION,
        ActorRole.DEVELOPER,
        "task",
        "prompt",
        tmp_path,
        CodexSandbox.UNRESTRICTED,
        thread_id=thread,
        worker_project=tmp_path,
    )
    with patch(
        "driver_port_factory.codex.gateway.execute",
        return_value=subprocess.CompletedProcess([], 0, '{"type":"turn.completed"}\n', ""),
    ) as execute:
        CodexExecGateway().run(job)
    argv = execute.call_args.args[0]
    config = tomllib.loads("\n".join(argv[i + 1] for i, a in enumerate(argv) if a == "-c"))
    env = config["shell_environment_policy"]["set"]
    assert env == {
        "DPF_WORKER_PROJECT": str(tmp_path),
        "DPF_WORKER_JOB": job.job_id,
        "DPF_WORKER_STAGE": S.DRIVER_IMPLEMENTATION.value,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    args = parser().parse_args(
        [
            "codex",
            "submit",
            str(tmp_path),
            job.stage.value,
            "--file",
            "report.md",
            "--kind",
            "report",
            "--decision",
            "pass",
        ]
    )
    assert args.job_id == job.job_id
    for command, options in [("run", ["--suite"]), ("acknowledge", ["--report", "report.md"])]:
        args = parser().parse_args(["experiment", command, str(tmp_path), *options])
        assert args.job_id == job.job_id


def test_cas_reference_resolves_controller_storage_and_wrong_type_fails(
    tmp_path, monkeypatch, capsys
):
    import hashlib

    refs = registry(tmp_path)
    monkeypatch.setenv("DPF_WORKER_PROJECT", str(tmp_path))
    data = b"bound controller evidence"
    digest = hashlib.sha256(data).hexdigest()
    path = "objects/sha256/" + digest[:2] + "/" + digest[2:]
    file = tmp_path / ".dpf" / "cas" / path
    file.parent.mkdir(parents=True)
    file.write_bytes(data)
    token = refs.put("provenance", {"cas_path": path, "digest": digest})
    assert read_main(["--ref", token]) == 0
    assert json.loads(capsys.readouterr().out)["excerpts"][0]["text"] == data.decode()
    assert read_main(["--cursor", token]) == 2
    assert "wrong type" in json.loads(capsys.readouterr().out)["reason"]
