"""Optional, premise-bound observations in the verified platform, never a PASS oracle."""

import json
from dataclasses import asdict

from ..core.events import RunEvent
from ..core.models import ArtifactContent, WorkflowError
from ..platform import executor, service
from . import route
from . import route_model as model
from .implementation import worktree_files


def premise(index, text, name):
    rows = [p for p in index["premises"] if p.get("id") == name]
    if len(rows) != 1:
        raise WorkflowError("A minimal probe must reference an existing critical premise")
    row = rows[0]
    return {"id": name, "question": model.section(text, row.get("question_section"))}


def inputs(project):
    service.verified(project)
    profile, worktree = service.load(project)
    service.check_image(project, profile)
    return {
        "profile": model.digest(profile),
        "files": worktree_files(worktree, profile["target_revision"]),
    }


def receipt(project, name):
    for event in route.events(project, RunEvent.ROUTE_PROBE):
        if event["id"] == name:
            return json.loads(project.artifacts.read(ArtifactContent(**event["content"])))
    raise WorkflowError(f"Unknown controller probe receipt: {name}")


def verify_references(project, index, text, *, previous=None):
    retained = {}
    if previous:
        old, old_text = previous
        retained = {
            name: premise(old["index"], old_text, p["id"])
            for p in old["index"]["premises"]
            for name in p["probe_receipts"]
        }
    checks = model.Checks()
    for row in index["premises"]:
        names = row.get("probe_receipts", [])
        if not isinstance(names, list):
            continue  # Schema diagnostics report malformed lists.
        for name in names:
            if not isinstance(name, str):
                continue
            checks.check(
                f"{row.get('id', '?')}.probe_receipts",
                _verify_reference,
                project,
                index,
                text,
                row,
                name,
                retained,
            )
    checks.finish()


def _verify_reference(project, index, text, row, name, retained):
    value = receipt(project, name)
    if (
        value["status"] != "OBSERVED"
        or value["premise"] != premise(index, text, row.get("id"))
        or (retained.get(name) != value["premise"] and value["inputs"] != inputs(project))
    ):
        raise WorkflowError(f"Failed or stale critical-premise observation: {name}")
    for item in value["evidence"]:
        project.artifacts.read(ArtifactContent(**item))


def run(project, report, name, script, *, timeout=120):
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise WorkflowError("A minimal probe has a 1..300 second explicit deadline")
    report = (project.root / report).resolve()
    if not report.is_relative_to(project.root):
        raise WorkflowError("Probe analysis must be a workspace report")
    index, text = route.read_index(report), report.read_text()
    index = model.validate(index, text)
    question = premise(index, text, name)
    before = inputs(project)
    profile, worktree = service.load(project)
    script = (worktree / script).resolve()
    if not script.is_relative_to(worktree) or not script.is_file():
        raise WorkflowError("Probe script must be a target-worktree file")
    script_bytes = script.read_bytes()
    script_ref = project.artifacts.put_bytes(script_bytes, kind="route_probe_script")
    identity = model.digest(
        {"premise": question, "inputs": before, "script": script_ref.digest, "timeout": timeout}
    )
    previous = route.events(project, RunEvent.ROUTE_PROBE)
    for event in previous:
        value = receipt(project, event["id"])
        if value["identity"] == identity:
            return {
                "id": event["id"],
                "status": value["status"],
                "reused": True,
                "instruction": "Identical inputs already observed; inspect the archived result.",
                "result": value.get("result"),
                "error": value.get("error"),
            }
    name_id = f"P{len(previous) + 1}"
    with service.locked(project) as directory:
        attempt = directory / "route-probes" / name_id
        attempt.mkdir(parents=True, exist_ok=False)
        value = {
            "identity": identity,
            "premise": question,
            "inputs": before,
            "status": "FAILED",
            "script": asdict(script_ref),
            "timeout": timeout,
        }
        try:
            result = executor.container(
                profile,
                worktree,
                attempt,
                ["bash", "-e", str(script)],
                build=False,
                cache_key=profile["cache_key"],
                timeout=timeout,
            )
            value["result"] = asdict(result)
            if inputs(project) != before or script.read_bytes() != script_bytes:
                raise WorkflowError("Probe changed source or script inputs; observation rejected")
            if result.exit_code == 0 and not result.timed_out:
                value["status"] = "OBSERVED"
        except BaseException as error:
            value["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            value["evidence"] = [
                asdict(project.artifacts.put_bytes(p.read_bytes(), kind="route_probe_log"))
                for p in sorted(attempt.rglob("*"))
                if p.is_file()
            ]
            ref = project.artifacts.put_bytes(route.encoded(value), kind="route_probe_receipt")
            project.record_event(RunEvent.ROUTE_PROBE, {"id": name_id, "content": asdict(ref)})
    return {
        "id": name_id,
        "status": value["status"],
        "result": value.get("result"),
        "log_root": str(attempt),
        "scope": "Observed script result only; interpret the premise, not driver acceptance",
    }
