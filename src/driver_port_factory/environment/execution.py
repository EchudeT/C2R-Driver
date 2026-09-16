from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from ..core.models import (
    ActorRole,
    ArtifactDirection,
    FileArtifact,
    StageStatus,
    WorkflowError,
    utc_now,
)
from ..core.project import Project
from .contracts import (
    EnvironmentArtifact,
    EnvironmentStage,
    ExperimentRouteMilestone,
    QmpDirection,
    QmpHandshakeStatus,
)
from .documents import json_artifact, json_bytes, plan_path
from .evidence import (
    file_identity,
    freeze_qemu_executable,
    frozen_repository_snapshot,
    workspace_path,
)
from .models import ExperimentPlan, ExperimentReadiness

CAPABILITIES_ID = "dpf-qmp-capabilities"
QUIT_ID = "dpf-qmp-quit"


@dataclass(frozen=True, slots=True)
class EnvironmentRunResult:
    route_id: str
    readiness: ExperimentReadiness
    stage_status: StageStatus
    attempt_path: str
    message: str


class ExperimentExecutor:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def run(self, project: Project, route_id: str) -> EnvironmentRunResult:
        project.ensure_role(*self.ROLES)
        if project.stage(EnvironmentStage.RECOVERY).status is not StageStatus.RUNNING:
            raise WorkflowError("environment_recovery is not RUNNING")
        plan = self._load_plan(project, route_id)
        self._require_frozen_inputs(project, plan)
        attempt_path = project.control / "environment" / "attempts" / f"{route_id}.json"
        if attempt_path.exists():
            raise WorkflowError(f"route {route_id} already ran; retries require a new route ID")
        attempt = self._execute_qmp(project, plan)
        readiness = self._final_gate(project, plan, attempt)
        attempt.update(readiness=readiness.value, recorded_at=utc_now())
        attempt_path.parent.mkdir(parents=True, exist_ok=True)
        attempt_path.write_bytes(json_bytes(attempt))
        project.record_artifact(
            EnvironmentStage.RECOVERY,
            FileArtifact(EnvironmentArtifact.RECOVERY_ATTEMPT, attempt_path),
        )
        if readiness is ExperimentReadiness.FAIL:
            return EnvironmentRunResult(
                route_id,
                readiness,
                StageStatus.RUNNING,
                str(attempt_path),
                "real QEMU/QMP proof failed; plan a distinct retry",
            )
        self._finalize(project, plan, attempt_path, attempt["runner_evidence"])
        return EnvironmentRunResult(
            route_id,
            readiness,
            StageStatus.PASS,
            str(attempt_path),
            "EXPERIMENT_READY proven by QMP; migrated-driver runtime is unproven",
        )

    @staticmethod
    def _load_plan(project: Project, route_id: str) -> ExperimentPlan:
        controlled = plan_path(project, route_id)
        if not controlled.is_file():
            raise WorkflowError(f"unknown environment route: {route_id}")
        data = controlled.read_bytes()
        matching = (
            ref
            for ref in project.artifact_refs(
                stage=EnvironmentStage.RECOVERY,
                direction=ArtifactDirection.INPUT,
            )
            if ref.kind == EnvironmentArtifact.EXPERIMENT_PLAN.value
            and ref.source == str(controlled)
            and project.artifacts.read(ref) == data
        )
        if sum(1 for _ in matching) != 1:
            raise WorkflowError("controlled experiment plan differs from its frozen artifact")
        try:
            plan = ExperimentPlan.from_dict(json.loads(data))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("controlled experiment plan is invalid JSON") from error
        if plan.route_id != route_id or plan.executable_lock is None:
            raise WorkflowError("controlled experiment plan has invalid frozen identity")
        return plan

    @staticmethod
    def _require_frozen_inputs(project: Project, plan: ExperimentPlan) -> None:
        if frozen_repository_snapshot(project) != plan.frozen_repositories:
            raise WorkflowError("frozen repositories changed after route registration")
        current = freeze_qemu_executable(
            project, plan.command[0], workspace_path(project, plan.cwd)
        )
        if current != plan.executable_lock:
            raise WorkflowError("QEMU executable identity changed after route registration")

    @staticmethod
    def _execute_qmp(project: Project, plan: ExperimentPlan) -> dict[str, Any]:
        cwd = workspace_path(project, plan.cwd)
        run_dir = project.control / "command-runs" / "environment" / plan.route_id
        run_dir.mkdir(parents=True, exist_ok=False)
        stdout_path, stderr_path = run_dir / "stdout.bin", run_dir / "stderr.bin"
        transcript_path = run_dir / "qmp-transcript.json"
        socket_id = hashlib.sha256(f"{project.root}:{plan.route_id}".encode()).hexdigest()[:24]
        socket_path = Path(tempfile.gettempdir()) / f"dpf-qmp-{socket_id}.sock"
        if socket_path.exists():
            raise WorkflowError(f"QMP socket endpoint already exists: {socket_path}")
        qmp_argument = f"unix:{socket_path},server=on,wait=off"
        argv = [plan.executable_lock["resolved"], *plan.command[1:], "-qmp", qmp_argument]
        transcript: list[dict[str, Any]] = []
        process: subprocess.Popen[bytes] | None = None
        launch_error = handshake_error = None
        timed_out = False
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env={**os.environ, **plan.environment},
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                )
                ExperimentExecutor._exchange_qmp(
                    process, socket_path, plan.timeout_seconds, transcript
                )
            except OSError as error:
                launch_error = f"{type(error).__name__}: {error}"
            except (TimeoutError, TypeError, ValueError, json.JSONDecodeError) as error:
                handshake_error = f"{type(error).__name__}: {error}"
            finally:
                if process is not None and process.poll() is None:
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        process.terminate()
                        process.wait(timeout=2)
        transcript_path.write_bytes(json_bytes({"entries": transcript}))
        process_record = {
            "argv": argv,
            "cwd": str(cwd),
            "pid": process.pid if process else None,
            "launched": process is not None,
            "launch_error": launch_error,
            "exit_code": process.returncode if process else None,
            "timed_out": timed_out,
            **ExperimentExecutor._file_evidence("stdout", stdout_path),
            **ExperimentExecutor._file_evidence("stderr", stderr_path),
        }
        qmp_record = {
            "socket_family": "AF_UNIX",
            "socket_path": str(socket_path),
            "argument": qmp_argument,
            "handshake_error": handshake_error,
            **ExperimentExecutor._file_evidence("transcript", transcript_path),
        }
        attempt = {
            "schema_version": 2,
            "route": plan.to_dict(),
            "frozen_repositories": plan.frozen_repositories,
            "process": process_record,
            "qmp": qmp_record,
            "runner_evidence": [
                file_identity(project, item) for item in plan.runner_evidence_paths
            ],
        }
        attempt["execution_evidence_sha256"] = ExperimentExecutor._evidence_digest(attempt)
        return attempt

    @staticmethod
    def _exchange_qmp(
        process: subprocess.Popen[bytes],
        socket_path: Path,
        timeout_seconds: int,
        transcript: list[dict[str, Any]],
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            while True:
                if process.poll() is not None:
                    raise ValueError("QEMU exited before opening QMP")
                try:
                    connection.connect(str(socket_path))
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("QEMU did not open QMP")
                    time.sleep(0.01)
            with connection.makefile("rb") as reader:
                greeting = ExperimentExecutor._receive(connection, reader, deadline)
                transcript.append({"direction": QmpDirection.RECEIVED, "message": greeting})
                capabilities = {"execute": "qmp_capabilities", "id": CAPABILITIES_ID}
                connection.sendall(json.dumps(capabilities).encode() + b"\r\n")
                transcript.append({"direction": QmpDirection.SENT, "message": capabilities})
                while True:
                    response = ExperimentExecutor._receive(connection, reader, deadline)
                    transcript.append({"direction": QmpDirection.RECEIVED, "message": response})
                    if response.get("id") == CAPABILITIES_ID:
                        break
                quit_command = {"execute": "quit", "id": QUIT_ID}
                connection.sendall(json.dumps(quit_command).encode() + b"\r\n")
        finally:
            connection.close()

    @staticmethod
    def _receive(connection: socket.socket, reader: BinaryIO, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("QMP response timed out")
        connection.settimeout(remaining)
        value = json.loads(reader.readline())
        if not isinstance(value, dict):
            raise TypeError("QMP response is not an object")
        return value

    @staticmethod
    def _final_gate(
        project: Project, plan: ExperimentPlan, attempt: dict[str, Any]
    ) -> ExperimentReadiness:
        process, qmp = attempt["process"], attempt["qmp"]
        unchanged = (
            attempt["frozen_repositories"] == plan.frozen_repositories
            and frozen_repository_snapshot(project) == plan.frozen_repositories
            and freeze_qemu_executable(project, plan.command[0], workspace_path(project, plan.cwd))
            == plan.executable_lock
            and attempt["execution_evidence_sha256"] == ExperimentExecutor._evidence_digest(attempt)
        )
        files_match = all(
            ExperimentExecutor._file_matches(record, name)
            for record, name in ((process, "stdout"), (process, "stderr"), (qmp, "transcript"))
        )
        try:
            entries = json.loads(Path(qmp["transcript_path"]).read_bytes())["entries"]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            entries = None
        handshake = ExperimentExecutor._handshake_status(entries)
        qmp["handshake_status"] = handshake.value
        attempt["exit_accepted"] = process["exit_code"] in plan.accepted_exit_codes
        ready = all(
            (
                unchanged,
                files_match,
                process["launched"] is True,
                process["pid"] is not None,
                process["launch_error"] is None,
                process["timed_out"] is False,
                qmp["handshake_error"] is None,
                handshake is QmpHandshakeStatus.VERIFIED,
                attempt["exit_accepted"],
            )
        )
        return ExperimentReadiness.PASS if ready else ExperimentReadiness.FAIL

    @staticmethod
    def _handshake_status(entries: object) -> QmpHandshakeStatus:
        if not isinstance(entries, list) or len(entries) < 3:
            return QmpHandshakeStatus.FAIL
        greeting, request = entries[:2]
        if (
            greeting.get("direction") != QmpDirection.RECEIVED
            or not isinstance(greeting.get("message", {}).get("QMP"), dict)
            or request
            != {
                "direction": QmpDirection.SENT,
                "message": {"execute": "qmp_capabilities", "id": CAPABILITIES_ID},
            }
        ):
            return QmpHandshakeStatus.FAIL
        verified = any(
            entry.get("direction") == QmpDirection.RECEIVED
            and entry.get("message") == {"return": {}, "id": CAPABILITIES_ID}
            for entry in entries[2:]
        )
        return QmpHandshakeStatus.VERIFIED if verified else QmpHandshakeStatus.FAIL

    @staticmethod
    def _file_evidence(name: str, path: Path) -> dict[str, str]:
        return {
            f"{name}_path": str(path),
            f"{name}_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    @staticmethod
    def _file_matches(record: dict[str, Any], name: str) -> bool:
        path = Path(record[f"{name}_path"])
        return (
            path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == record[f"{name}_sha256"]
        )

    @staticmethod
    def _evidence_digest(attempt: dict[str, Any]) -> str:
        qmp = dict(attempt["qmp"])
        qmp.pop("handshake_status", None)
        evidence = {key: attempt[key] for key in ("route", "frozen_repositories", "process")}
        evidence["qmp"] = qmp
        return hashlib.sha256(json_bytes(evidence)).hexdigest()

    @staticmethod
    def _finalize(
        project: Project,
        plan: ExperimentPlan,
        attempt_path: Path,
        evidence: list[dict[str, object]],
    ) -> None:
        artifact_mode = {
            "schema_version": 2,
            "artifact_mode": plan.artifact_mode,
            "route_kind": plan.route_kind,
            "selected_route_id": plan.route_id,
            "compiled_reused_injected_or_prebuilt": (
                "environment smoke only; migrated-driver payload identity is established later"
            ),
            "driver_insertion_or_packaging_path": plan.driver_insertion_or_packaging_path,
            "runner_evidence": evidence,
        }
        route = {
            "schema_version": 2,
            "milestone": ExperimentRouteMilestone.READY,
            "route_id": plan.route_id,
            "device_identity": plan.device_identity,
            "topology": plan.topology,
            "artifact_mode": plan.artifact_mode,
            "route_kind": plan.route_kind,
            "command": list(plan.command),
            "attempt_sha256": hashlib.sha256(attempt_path.read_bytes()).hexdigest(),
            "migrated_driver_runtime_ready": False,
        }
        inventory = project.load_json_artifact(
            EnvironmentStage.RECOVERY,
            EnvironmentArtifact.INVENTORY,
            direction=ArtifactDirection.INPUT,
        )
        candidates = project.load_json_artifact(
            EnvironmentStage.RECOVERY,
            EnvironmentArtifact.MODE_CANDIDATES,
            direction=ArtifactDirection.INPUT,
        )
        project.finalize_stage(
            EnvironmentStage.RECOVERY,
            (
                json_artifact(EnvironmentArtifact.INVENTORY, inventory),
                json_artifact(EnvironmentArtifact.MODE_CANDIDATES, candidates),
                json_artifact(EnvironmentArtifact.MODE_RECORD, artifact_mode),
                FileArtifact(EnvironmentArtifact.EXPERIMENT_READY_RUN, attempt_path),
                json_artifact(EnvironmentArtifact.EXPERIMENT_ROUTE, route),
            ),
        )
