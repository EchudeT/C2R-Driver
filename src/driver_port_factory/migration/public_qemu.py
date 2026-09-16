from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from ..acquisition.material import parse_materials
from ..core.contracts import ArtifactKey
from ..core.execution import CommandResult, CommandRunner
from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..environment.contracts import (
    EnvironmentArtifact,
    EnvironmentStage,
    QmpDirection,
    QmpHandshakeStatus,
)
from ..environment.evidence import freeze_qemu_executable, workspace_path
from ..environment.execution import CAPABILITIES_ID, QUIT_ID, ExperimentExecutor
from ..knowledge.contracts import KnowledgeArtifact, KnowledgeDomain
from ..knowledge.corpus import CorpusManifest
from ..knowledge.index import KnowledgeIndex, file_sha256
from ..source_analysis.contracts import SourceAnalysisArtifact, SourceAnalysisStage
from .artifact_preparation import PlannedCommand
from .contracts import (
    ContractEvidenceStatus,
    ContractExecutionStatus,
    ContractVerificationKind,
    EvidenceLadderLevel,
    LadderDisposition,
    MigrationArtifact,
    MigrationStage,
    PublicRunAttribution,
    TestDisposition,
)

PUBLIC_QEMU_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.CONTRACTS,
    MigrationArtifact.TEST_PORT_MATRIX,
    MigrationArtifact.IMPLEMENTATION_BUNDLE,
    MigrationArtifact.COMPLIANCE_REPORT,
    MigrationArtifact.RUNTIME_ARTIFACT,
    MigrationArtifact.ARTIFACT_IDENTITY,
    EnvironmentArtifact.EXPERIMENT_READY_RUN,
    EnvironmentArtifact.EXPERIMENT_ROUTE,
    KnowledgeArtifact.QUERY_CONTRACT,
    SourceAnalysisArtifact.MATERIALS_MANIFEST,
)


def _strings(value: Any, label: str, *, empty: bool = False) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not empty and not value)
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise WorkflowError(f"public QEMU plan {label} must be a string list")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class PublicOracle:
    device_identity: str
    direction: str
    length: int
    payload_sha256: str

    @classmethod
    def from_dict(cls, value: Any) -> PublicOracle:
        if not isinstance(value, dict):
            raise WorkflowError("public QEMU oracle must be an object")
        try:
            result = cls(
                str(value["device_identity"]),
                str(value["direction"]),
                int(value["length"]),
                str(value["payload_sha256"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("public QEMU oracle has an invalid typed boundary") from error
        if (
            not result.device_identity
            or not result.direction
            or result.length < 0
            or len(result.payload_sha256) != 64
        ):
            raise WorkflowError("public QEMU oracle is incomplete")
        return result

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PublicRun:
    run_id: str
    purpose: str
    contract_ids: tuple[str, ...]
    test_ids: tuple[str, ...]
    artifact_sha256: str
    implementation_sha256: str
    packaged_test_sha256: str
    device_identity: str
    topology: str
    cpu: str
    memory: str
    backend: str
    cwd: str
    qemu: PlannedCommand
    stimulus: PlannedCommand
    checker: PlannedCommand
    oracle: PublicOracle
    cleanup: str
    qemu_evidence: tuple[dict[str, Any], ...]

    @classmethod
    def from_dict(cls, value: Any) -> PublicRun:
        if not isinstance(value, dict):
            raise WorkflowError("public QEMU run must be an object")
        try:
            evidence = value["qemu_evidence"]
            if not isinstance(evidence, list) or not all(
                isinstance(item, dict) for item in evidence
            ):
                raise TypeError
            return cls(
                str(value["run_id"]),
                str(value["purpose"]),
                _strings(value["contract_ids"], "contract_ids", empty=True),
                _strings(value["test_ids"], "test_ids", empty=True),
                str(value["artifact_sha256"]),
                str(value["implementation_sha256"]),
                str(value["packaged_test_sha256"]),
                str(value["device_identity"]),
                str(value["topology"]),
                str(value["cpu"]),
                str(value["memory"]),
                str(value["backend"]),
                str(value["cwd"]),
                PlannedCommand.from_dict(value["qemu"]),
                PlannedCommand.from_dict(value["stimulus"]),
                PlannedCommand.from_dict(value["checker"]),
                PublicOracle.from_dict(value["oracle"]),
                str(value["cleanup"]),
                tuple(evidence),
            )
        except (KeyError, TypeError) as error:
            raise WorkflowError("public QEMU run has an invalid typed boundary") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "purpose": self.purpose,
            "contract_ids": list(self.contract_ids),
            "test_ids": list(self.test_ids),
            "artifact_sha256": self.artifact_sha256,
            "implementation_sha256": self.implementation_sha256,
            "packaged_test_sha256": self.packaged_test_sha256,
            "device_identity": self.device_identity,
            "topology": self.topology,
            "cpu": self.cpu,
            "memory": self.memory,
            "backend": self.backend,
            "cwd": self.cwd,
            "qemu": self.qemu.to_dict(),
            "stimulus": self.stimulus.to_dict(),
            "checker": self.checker.to_dict(),
            "oracle": self.oracle.to_dict(),
            "cleanup": self.cleanup,
            "qemu_evidence": list(self.qemu_evidence),
        }


@dataclass(frozen=True, slots=True)
class LadderPlan:
    level: EvidenceLadderLevel
    disposition: LadderDisposition
    run_ids: tuple[str, ...]
    rationale: str
    qemu_evidence: tuple[dict[str, Any], ...]

    @classmethod
    def from_dict(cls, value: Any) -> LadderPlan:
        if not isinstance(value, dict):
            raise WorkflowError("public evidence-ladder item must be an object")
        try:
            evidence = value["qemu_evidence"]
            if not isinstance(evidence, list) or not all(
                isinstance(item, dict) for item in evidence
            ):
                raise TypeError
            return cls(
                EvidenceLadderLevel(value["level"]),
                LadderDisposition(value["disposition"]),
                _strings(value["run_ids"], "run_ids", empty=True),
                str(value["rationale"]),
                tuple(evidence),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("public evidence-ladder item has an invalid boundary") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "disposition": self.disposition.value,
            "run_ids": list(self.run_ids),
            "rationale": self.rationale,
            "qemu_evidence": list(self.qemu_evidence),
        }


@dataclass(frozen=True, slots=True)
class PublicQemuPlan:
    runs: tuple[PublicRun, ...]
    ladder: tuple[LadderPlan, ...]

    @classmethod
    def read(cls, path: Path) -> PublicQemuPlan:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("Codex public QEMU plan is not UTF-8 JSON") from error
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Any) -> PublicQemuPlan:
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise WorkflowError("public QEMU plan must be a schema_version=1 object")
        try:
            runs, ladder = value["runs"], value["ladder"]
            if not isinstance(runs, list) or not runs or not isinstance(ladder, list):
                raise TypeError
            return cls(
                tuple(PublicRun.from_dict(item) for item in runs),
                tuple(LadderPlan.from_dict(item) for item in ladder),
            )
        except (KeyError, TypeError) as error:
            raise WorkflowError("public QEMU plan has an invalid typed boundary") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "runs": [run.to_dict() for run in self.runs],
            "ladder": [item.to_dict() for item in self.ladder],
        }


class PublicQemuService:
    def run(self, project: Project, plan: PublicQemuPlan) -> dict[str, Any]:
        if project.stage(MigrationStage.PUBLIC_QEMU_VALIDATION).status is not StageStatus.RUNNING:
            raise WorkflowError("public_qemu_validation must be RUNNING")
        inputs = {kind.value: self._input(project, kind).to_dict() for kind in PUBLIC_QEMU_INPUTS}
        artifact = self._artifact_binding(project, inputs)
        self._plan_gate(project, plan, inputs, artifact)
        plan_digest = hashlib.sha256(self._json(plan.to_dict())).hexdigest()
        attempt_dir = project.control / "public-qemu" / plan_digest[:20]
        attempt_dir.mkdir(parents=True, exist_ok=False)
        results = [self._execute_run(project, run, attempt_dir / run.run_id) for run in plan.runs]
        ladder = self._ladder_results(plan, results)
        report = {
            "schema_version": 1,
            "inputs": inputs,
            "plan_sha256": plan_digest,
            "plan": plan.to_dict(),
            "artifact_identity": artifact,
            "runs": results,
            "ladder": ladder,
            "status": StageStatus.PASS.value
            if all(
                result["execution_status"] == ContractExecutionStatus.PASS.value
                for result in results
            )
            else StageStatus.FAIL.value,
            "recorded_at": utc_now(),
        }
        attempt_path = attempt_dir / "attempt.json"
        attempt_path.write_bytes(self._json(report))
        attempt_ref = project.record_artifact(
            MigrationStage.PUBLIC_QEMU_VALIDATION,
            FileArtifact(MigrationArtifact.PUBLIC_QEMU_ATTEMPT, attempt_path),
        )
        if report["status"] != StageStatus.PASS.value:
            return {"status": StageStatus.RUNNING.value, "attempt": str(attempt_path)}
        report["attempt_sha256"] = attempt_ref.digest
        project.finalize_stage(
            MigrationStage.PUBLIC_QEMU_VALIDATION,
            (
                GeneratedArtifact(
                    MigrationArtifact.PUBLIC_QEMU_REPORT,
                    self._json(report),
                    f"generated:public-qemu:{plan_digest}",
                ),
            ),
        )
        return {"status": StageStatus.PASS.value, "attempt": str(attempt_path)}

    def _plan_gate(
        self,
        project: Project,
        plan: PublicQemuPlan,
        inputs: dict[str, Any],
        artifact: dict[str, str],
    ) -> None:
        run_ids = [run.run_id for run in plan.runs]
        if len(run_ids) != len(set(run_ids)):
            raise WorkflowError("public QEMU run IDs must be unique")
        if [item.level for item in plan.ladder] != list(EvidenceLadderLevel):
            raise WorkflowError("public QEMU plan must freeze the ordered evidence ladder")
        known = set(run_ids)
        for item in plan.ladder:
            if not item.rationale.strip() or not item.qemu_evidence:
                raise WorkflowError("public QEMU ladder rationale/evidence is incomplete")
            if item.disposition is LadderDisposition.EXECUTE and not item.run_ids:
                raise WorkflowError("executed evidence-ladder level has no run")
            if item.disposition is LadderDisposition.BLOCKED or not set(item.run_ids) <= known:
                raise WorkflowError("blocked or unknown public QEMU ladder work cannot execute")
        regression = next(
            item for item in plan.ladder if item.level is EvidenceLadderLevel.REGRESSION
        )
        if regression.disposition is LadderDisposition.EXECUTE and len(regression.run_ids) < 2:
            raise WorkflowError("regression evidence requires repeated clean QEMU runs")

        route = project.load_json_artifact(
            EnvironmentStage.RECOVERY,
            EnvironmentArtifact.EXPERIMENT_ROUTE,
        )
        runtime_path = str(project.artifacts.path_for_digest(artifact["artifact_sha256"]))
        for run in plan.runs:
            if (
                run.artifact_sha256 != artifact["artifact_sha256"]
                or run.implementation_sha256 != artifact["implementation_sha256"]
                or run.packaged_test_sha256 != artifact["packaged_test_sha256"]
                or run.qemu.argv[0] != route["command"][0]
                or runtime_path not in run.qemu.argv
                or run.device_identity != route["device_identity"]
                or run.topology != route["topology"]
                or "-qmp" in run.qemu.argv
                or "-monitor" in run.qemu.argv
            ):
                raise WorkflowError("public QEMU run is not bound to the frozen route/artifact")
        contracts = self._expected_contracts(project)
        tests = self._expected_tests(project)
        if {item for run in plan.runs for item in run.contract_ids} != contracts or {
            item for run in plan.runs for item in run.test_ids
        } != tests:
            raise WorkflowError("public QEMU plan does not cover every applicable contract/test")
        self._verify_qemu_evidence(project, plan, inputs)

    def _execute_run(self, project: Project, run: PublicRun, run_dir: Path) -> dict[str, Any]:
        run_dir.mkdir(parents=True, exist_ok=False)
        cwd = workspace_path(project, run.cwd)
        qemu_lock = freeze_qemu_executable(project, run.qemu.argv[0], cwd)
        socket_id = hashlib.sha256(f"{project.root}:{run.run_id}".encode()).hexdigest()[:24]
        socket_path = Path(tempfile.gettempdir()) / f"dpf-public-{socket_id}.sock"
        if socket_path.exists():
            raise WorkflowError(f"public QMP endpoint already exists: {socket_path}")
        stdout_path, stderr_path = run_dir / "serial.bin", run_dir / "stderr.bin"
        transcript_path = run_dir / "qmp.json"
        argv = [
            str(qemu_lock["resolved"]),
            *run.qemu.argv[1:],
            "-qmp",
            f"unix:{socket_path},server=on,wait=off",
        ]
        transcript: list[dict[str, Any]] = []
        process: subprocess.Popen[bytes] | None = None
        stimulus = checker = None
        actual = None
        error = None
        timed_out = False
        started = time.monotonic()
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env={**os.environ, **run.qemu.environment},
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                )
                connection, reader = self._qmp(
                    process, socket_path, run.qemu.timeout_seconds, transcript
                )
                runner = CommandRunner(run_dir / "commands")
                stimulus = runner.run(
                    run.stimulus.argv,
                    cwd=cwd,
                    environment=run.stimulus.environment,
                    timeout_seconds=run.stimulus.timeout_seconds,
                )
                checker = runner.run(
                    run.checker.argv,
                    cwd=cwd,
                    environment=run.checker.environment,
                    timeout_seconds=run.checker.timeout_seconds,
                )
                if self._command_passed(checker, run.checker):
                    actual = json.loads(Path(checker.stdout_path).read_text(encoding="utf-8"))
                connection.sendall(
                    json.dumps({"execute": "quit", "id": QUIT_ID}).encode() + b"\r\n"
                )
                reader.close()
                connection.close()
                process.wait(timeout=run.qemu.timeout_seconds)
            except (
                OSError,
                TimeoutError,
                subprocess.TimeoutExpired,
                json.JSONDecodeError,
                ValueError,
            ) as failure:
                error = f"{type(failure).__name__}: {failure}"
            finally:
                if process is not None and process.poll() is None:
                    timed_out = True
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
        transcript_path.write_bytes(self._json({"entries": transcript}))
        handshake = ExperimentExecutor._handshake_status(transcript)
        oracle_pass = self._oracle_pass(run, actual)
        stimulus_pass = stimulus is not None and self._command_passed(stimulus, run.stimulus)
        checker_pass = checker is not None and self._command_passed(checker, run.checker)
        qemu_pass = (
            process is not None
            and process.returncode in run.qemu.accepted_exit_codes
            and not timed_out
            and error is None
            and handshake is QmpHandshakeStatus.VERIFIED
        )
        passed = qemu_pass and stimulus_pass and checker_pass and oracle_pass
        controls = (
            isinstance(actual, dict)
            and actual.get("positive_control") is True
            and actual.get("negative_control") is True
        )
        attribution = (
            PublicRunAttribution.TARGET_DRIVER_ON_QEMU
            if passed or (qemu_pass and stimulus_pass and checker_pass and controls)
            else PublicRunAttribution.PUBLIC_HARNESS
            if qemu_pass
            else PublicRunAttribution.ENVIRONMENT
        )
        return {
            "run_id": run.run_id,
            "contract_ids": list(run.contract_ids),
            "test_ids": list(run.test_ids),
            "qemu": {
                "argv": argv,
                "cwd": str(cwd),
                "pid": process.pid if process else None,
                "exit_code": process.returncode if process else None,
                "timed_out": timed_out,
                "duration_milliseconds": round((time.monotonic() - started) * 1000),
                "tool": qemu_lock,
                "stdout_path": str(stdout_path),
                "stdout_sha256": file_sha256(stdout_path),
                "stderr_path": str(stderr_path),
                "stderr_sha256": file_sha256(stderr_path),
                "qmp_path": str(transcript_path),
                "qmp_sha256": file_sha256(transcript_path),
                "qmp_handshake": handshake.value,
                "error": error,
            },
            "stimulus": asdict(stimulus) if stimulus else None,
            "checker": asdict(checker) if checker else None,
            "expected": run.oracle.to_dict(),
            "actual": actual,
            "controls_valid": controls,
            "evidence_status": ContractEvidenceStatus.VERIFIED.value
            if passed
            else ContractEvidenceStatus.UNKNOWN.value,
            "execution_status": ContractExecutionStatus.PASS.value
            if passed
            else ContractExecutionStatus.FAIL.value,
            "attribution": attribution.value,
        }

    @staticmethod
    def _qmp(
        process: subprocess.Popen[bytes],
        socket_path: Path,
        timeout_seconds: int,
        transcript: list[dict[str, Any]],
    ) -> tuple[socket.socket, BinaryIO]:
        deadline = time.monotonic() + timeout_seconds
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
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
        reader = connection.makefile("rb")
        greeting = ExperimentExecutor._receive(connection, reader, deadline)
        transcript.append({"direction": QmpDirection.RECEIVED, "message": greeting})
        request = {"execute": "qmp_capabilities", "id": CAPABILITIES_ID}
        connection.sendall(json.dumps(request).encode() + b"\r\n")
        transcript.append({"direction": QmpDirection.SENT, "message": request})
        while True:
            response = ExperimentExecutor._receive(connection, reader, deadline)
            transcript.append({"direction": QmpDirection.RECEIVED, "message": response})
            if response.get("id") == CAPABILITIES_ID:
                return connection, reader

    @staticmethod
    def _oracle_pass(run: PublicRun, actual: Any) -> bool:
        return isinstance(actual, dict) and all(
            actual.get(field) == expected
            for field, expected in {
                **run.oracle.to_dict(),
                "artifact_sha256": run.artifact_sha256,
                "driver_path_observed": True,
                "positive_control": True,
                "negative_control": True,
            }.items()
        )

    @staticmethod
    def _command_passed(result: CommandResult, plan: PlannedCommand) -> bool:
        return (
            result.launched
            and result.launch_error is None
            and not result.timed_out
            and result.exit_code in plan.accepted_exit_codes
        )

    @staticmethod
    def _ladder_results(
        plan: PublicQemuPlan, results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        by_id = {result["run_id"]: result for result in results}
        values = []
        for item in plan.ladder:
            passed = item.disposition is not LadderDisposition.EXECUTE or all(
                by_id[run_id]["execution_status"] == ContractExecutionStatus.PASS.value
                for run_id in item.run_ids
            )
            values.append(
                {
                    **item.to_dict(),
                    "evidence_status": ContractEvidenceStatus.VERIFIED.value
                    if passed
                    else ContractEvidenceStatus.UNKNOWN.value,
                    "execution_status": ContractExecutionStatus.NOT_APPLICABLE.value
                    if item.disposition is LadderDisposition.NOT_APPLICABLE
                    else ContractExecutionStatus.PASS.value
                    if passed
                    else ContractExecutionStatus.FAIL.value,
                }
            )
        return values

    @staticmethod
    def _artifact_binding(project: Project, inputs: dict[str, Any]) -> dict[str, str]:
        project.verify_integrity()
        identity = project.load_json_artifact(
            MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY
        )
        runtime = project.artifact(
            MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT
        )
        implementation = inputs[MigrationArtifact.IMPLEMENTATION_BUNDLE.value]["digest"]
        presence = identity["driver_presence"]["manifest"]
        packaged = identity["packaged_test_artifact"]
        packaged_relative = PurePosixPath(str(packaged["path"]))
        if (
            identity["runtime_artifact"]["sha256"] != runtime.digest
            or presence["implementation_bundle_sha256"] != implementation
            or packaged_relative.is_absolute()
            or ".." in packaged_relative.parts
            or packaged_relative.as_posix() != packaged["path"]
            or file_sha256(workspace_path(project, packaged["path"])) != packaged["sha256"]
        ):
            raise WorkflowError("current runtime artifact identity/presence is invalid")
        return {
            "artifact_sha256": runtime.digest,
            "implementation_sha256": implementation,
            "packaged_test_sha256": packaged["sha256"],
        }

    @staticmethod
    def _expected_contracts(project: Project) -> set[str]:
        document = project.load_json_artifact(MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS)
        return {
            str(item["id"])
            for item in document["contracts"]
            if item["verification"]["kind"] == ContractVerificationKind.QEMU.value
        }

    @staticmethod
    def _expected_tests(project: Project) -> set[str]:
        document = project.load_json_artifact(
            MigrationStage.TEST_ADAPTATION, MigrationArtifact.TEST_PORT_MATRIX
        )
        return {
            str(item["test_id"])
            for item in document["tests"]
            if item["disposition"] in {TestDisposition.RETAIN.value, TestDisposition.ADAPT.value}
        }

    @staticmethod
    def _verify_qemu_evidence(
        project: Project, plan: PublicQemuPlan, inputs: dict[str, Any]
    ) -> None:
        ref = project.artifact(
            SourceAnalysisStage.SOURCE_CLOSURE,
            SourceAnalysisArtifact.MATERIALS_MANIFEST,
        )
        data = project.artifacts.read(ref)
        knowledge = KnowledgeIndex(
            project.root, CorpusManifest(parse_materials(data), data, ref.digest, ref.source)
        )
        knowledge.status()
        references = [reference for run in plan.runs for reference in run.qemu_evidence]
        references.extend(reference for item in plan.ladder for reference in item.qemu_evidence)
        for reference in references:
            exact = knowledge.show(str(reference.get("chunk_id")))["result"]
            if (
                exact["domain"] != KnowledgeDomain.QEMU.value
                or reference.get("record_id") != exact["record_id"]
            ):
                raise WorkflowError("public QEMU plan evidence is not a pinned QEMU original")
            if file_sha256(knowledge.controlled_path(exact["path"])) != exact["sha256"]:
                raise WorkflowError("public QEMU evidence original changed")

    @staticmethod
    def _input(project: Project, kind: ArtifactKey):
        dependencies = project.workflow.spec(MigrationStage.PUBLIC_QEMU_VALIDATION).dependencies
        matches = [
            ref
            for stage in dependencies
            for ref in project.artifact_refs(stage=stage)
            if ref.kind == kind.value
        ]
        if len(matches) != 1:
            raise WorkflowError(f"public QEMU validation requires one {kind.value} input")
        return matches[0]

    @staticmethod
    def _json(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def validate_public_qemu_bundle(context: BundleValidationContext) -> None:
    report = json_object(
        context.one_current(MigrationArtifact.PUBLIC_QEMU_REPORT)[1],
        MigrationArtifact.PUBLIC_QEMU_REPORT.value,
    )
    attempt_ref, attempt_data = context.one_auxiliary(MigrationArtifact.PUBLIC_QEMU_ATTEMPT)
    attempt = json_object(attempt_data, MigrationArtifact.PUBLIC_QEMU_ATTEMPT.value)
    expected_inputs = {
        kind.value: context.one_dependency(kind)[0].to_dict() for kind in PUBLIC_QEMU_INPUTS
    }
    contracts = json_object(
        context.one_dependency(MigrationArtifact.CONTRACTS)[1], MigrationArtifact.CONTRACTS.value
    )
    matrix = json_object(
        context.one_dependency(MigrationArtifact.TEST_PORT_MATRIX)[1],
        MigrationArtifact.TEST_PORT_MATRIX.value,
    )
    expected_contracts = {
        str(item["id"])
        for item in contracts["contracts"]
        if item["verification"]["kind"] == ContractVerificationKind.QEMU.value
    }
    expected_tests = {
        str(item["test_id"])
        for item in matrix["tests"]
        if item["disposition"] in {TestDisposition.RETAIN.value, TestDisposition.ADAPT.value}
    }
    runs = report.get("runs", [])
    plan = PublicQemuPlan.from_dict(report.get("plan"))
    planned_runs = {run.run_id: run for run in plan.runs}
    if (
        report.get("inputs") != expected_inputs
        or report.get("artifact_identity") != _final_artifact_binding(context)
        or report.get("attempt_sha256") != attempt_ref.digest
        or attempt.get("status") != StageStatus.PASS.value
        or report.get("status") != StageStatus.PASS.value
        or {item for run in runs for item in run.get("contract_ids", [])} != expected_contracts
        or {item for run in runs for item in run.get("test_ids", [])} != expected_tests
        or any(
            run.get("execution_status") != ContractExecutionStatus.PASS.value
            or run.get("evidence_status") != ContractEvidenceStatus.VERIFIED.value
            or run.get("attribution") != PublicRunAttribution.TARGET_DRIVER_ON_QEMU.value
            or run.get("qemu", {}).get("qmp_handshake") != QmpHandshakeStatus.VERIFIED.value
            or run.get("controls_valid") is not True
            or run.get("actual") is None
            or run.get("run_id") not in planned_runs
            or run.get("expected") != planned_runs[run["run_id"]].oracle.to_dict()
            or not PublicQemuService._oracle_pass(planned_runs[run["run_id"]], run.get("actual"))
            for run in runs
        )
        or [item.get("level") for item in report.get("ladder", [])]
        != [level.value for level in EvidenceLadderLevel]
        or any(
            item.get("execution_status")
            not in {
                ContractExecutionStatus.PASS.value,
                ContractExecutionStatus.NOT_APPLICABLE.value,
            }
            for item in report.get("ladder", [])
        )
    ):
        raise WorkflowError("public QEMU evidence ladder is incomplete or unqualified")
    for run in runs:
        for section, names in (
            (run["qemu"], ("stdout", "stderr", "qmp")),
            (run["stimulus"], ("stdout", "stderr")),
            (run["checker"], ("stdout", "stderr")),
        ):
            for name in names:
                path = Path(section[f"{name}_path"]).resolve()
                if (
                    context.project_root not in path.parents
                    or file_sha256(path) != section[f"{name}_sha256"]
                ):
                    raise WorkflowError("public QEMU run evidence changed after execution")
        tool = run["qemu"].get("tool", {})
        executable = Path(str(tool.get("resolved", ""))).resolve()
        if not executable.is_file() or file_sha256(executable) != tool.get("sha256"):
            raise WorkflowError("public QEMU executable identity changed after execution")


def _final_artifact_binding(context: BundleValidationContext) -> dict[str, str]:
    runtime_ref, _ = context.one_dependency(MigrationArtifact.RUNTIME_ARTIFACT)
    implementation_ref, _ = context.one_dependency(MigrationArtifact.IMPLEMENTATION_BUNDLE)
    identity = json_object(
        context.one_dependency(MigrationArtifact.ARTIFACT_IDENTITY)[1],
        MigrationArtifact.ARTIFACT_IDENTITY.value,
    )
    packaged = identity.get("packaged_test_artifact", {})
    relative = PurePosixPath(str(packaged.get("path", "")))
    packaged_path = (context.project_root / relative).resolve()
    presence = identity.get("driver_presence", {}).get("manifest", {})
    if (
        identity.get("runtime_artifact", {}).get("sha256") != runtime_ref.digest
        or presence.get("implementation_bundle_sha256") != implementation_ref.digest
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != packaged.get("path")
        or context.project_root not in packaged_path.parents
        or not packaged_path.is_file()
        or file_sha256(packaged_path) != packaged.get("sha256")
    ):
        raise WorkflowError("final public QEMU artifact identity is invalid")
    return {
        "artifact_sha256": runtime_ref.digest,
        "implementation_sha256": implementation_ref.digest,
        "packaged_test_sha256": packaged["sha256"],
    }
