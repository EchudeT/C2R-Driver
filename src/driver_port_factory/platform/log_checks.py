"""Recheck positive boot-log facts from a controller-captured run; never reclassify that run."""

import json
import sqlite3
from dataclasses import asdict

from ..core.events import RunEvent
from ..core.models import ArtifactContent, WorkflowError
from ..migration.implementation import worktree_files
from . import guest, service
from .profile import digest


def _captures(project):
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as db:
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM events WHERE event_type=? ORDER BY sequence",
                (RunEvent.PLATFORM_CASE_CAPTURED.value,),
            )
        ]


def capture(project, directory, result, profile, files, artifact_sha256, case):
    path = directory / "boot-serial.log"
    if not path.is_file():
        return None  # A runner/environment failure without a captured stream cannot be replayed.
    serial = project.artifacts.put_bytes(path.read_bytes(), kind="platform_boot_log")
    value = {
        "profile": digest(profile),
        "files": files,
        "artifact_sha256": artifact_sha256,
        "case": case,
        "result": result,
        "raw_log": asdict(serial),
        "original_receipt": str(directory / "boot.json"),
    }
    ref = project.artifacts.put_bytes(
        json.dumps(value, sort_keys=True).encode(), kind="platform_log_capture"
    )
    name = f"T{len(_captures(project)) + 1}"
    project.record_event(RunEvent.PLATFORM_CASE_CAPTURED, {"id": name, "content": asdict(ref)})
    return name


def check(project, name, contains):
    if not isinstance(contains, list) or not contains or len(contains) > 32:
        raise WorkflowError("contains must list 1..32 literal boot messages")
    try:
        for text in contains:
            guest.validate_step({"assert_boot_log": text}, 120)
    except ValueError as error:
        raise WorkflowError(str(error)) from error
    service.active(project)
    with service.locked(project):
        service.presence(project)
        profile, worktree = service.load(project)
        service.check_image(project, profile)
        found = [e for e in _captures(project) if e["id"] == name]
        if len(found) != 1:
            raise WorkflowError(f"Unknown controller boot-log capture: {name}")
        content = ArtifactContent(**found[0]["content"])
        value = json.loads(project.artifacts.read(content))
        built = json.loads((service.root(project) / "current-build.json").read_text())
        if (
            value["profile"] != digest(profile)
            or value["artifact_sha256"] != built["published_sha256"]
            or value["files"] != worktree_files(worktree, profile["target_revision"])
        ):
            raise WorkflowError(
                "Capture belongs to earlier code/artifact/environment; run current case"
            )
        raw = project.artifacts.read(ArtifactContent(**value["raw_log"]))
        checks = [{"contains": text, "observed": text in guest.log_text(raw)} for text in contains]
        result = {
            "status": "BOOT_LOG_OBSERVED"
            if all(c["observed"] for c in checks)
            else "BOOT_LOG_MISSING",
            "capture": name,
            "checks": checks,
            "original_run_status": value["result"]["status"],
            "original_receipt": value["original_receipt"],
            "devices": value["case"].get("devices", []),
            "scope": "Positive facts in this captured pre-input boot stream only. "
            "No new execution, "
            "no case PASS, no absent-event, guest-command or driver acceptance claim.",
        }
        project.record_event(
            RunEvent.PLATFORM_LOG_CHECKED, {**result, "capture_content": asdict(content)}
        )
        return result
