from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..acquisition.material import parse_materials
from ..core.contracts import ArtifactKey
from ..core.execution import CommandResult, CommandRunner
from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..environment.contracts import EnvironmentArtifact
from ..environment.evidence import executable_identity, workspace_path
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
    MigrationBoundary,
    MigrationStage,
    PublicRunAttribution,
    TestDisposition,
)

PUBLIC_QEMU_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.CONTRACTS,
    MigrationArtifact.TEST_PORT_MATRIX,
    MigrationArtifact.RUNTIME_ARTIFACT,
    MigrationArtifact.ARTIFACT_IDENTITY,
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
class PublicRun:
    run_id: str
    purpose: str
    contract_ids: tuple[str, ...]
    test_ids: tuple[str, ...]
    cwd: str
    command: PlannedCommand
    oracle: str
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
            run = cls(
                str(value["run_id"]),
                str(value["purpose"]),
                _strings(value["contract_ids"], "contract_ids", empty=True),
                _strings(value["test_ids"], "test_ids", empty=True),
                str(value["cwd"]),
                PlannedCommand.from_dict(value["command"]),
                str(value["oracle"]),
                tuple(evidence),
            )
        except (KeyError, TypeError) as error:
            raise WorkflowError("public QEMU run has an invalid typed boundary") from error
        if not run.run_id or not run.purpose.strip() or not run.oracle.strip():
            raise WorkflowError("public QEMU run is incomplete")
        return run

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "purpose": self.purpose,
            "contract_ids": list(self.contract_ids),
            "test_ids": list(self.test_ids),
            "cwd": self.cwd,
            "command": self.command.to_dict(),
            "oracle": self.oracle,
            "qemu_evidence": list(self.qemu_evidence),
        }


@dataclass(frozen=True, slots=True)
class LadderPlan:
    level: EvidenceLadderLevel
    disposition: LadderDisposition
    run_ids: tuple[str, ...]
    rationale: str

    @classmethod
    def from_dict(cls, value: Any) -> LadderPlan:
        if not isinstance(value, dict):
            raise WorkflowError("public evidence-ladder item must be an object")
        try:
            item = cls(
                EvidenceLadderLevel(value["level"]),
                LadderDisposition(value["disposition"]),
                _strings(value["run_ids"], "run_ids", empty=True),
                str(value["rationale"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("public evidence-ladder item has an invalid boundary") from error
        if not item.rationale.strip():
            raise WorkflowError("public evidence-ladder item needs a rationale")
        return item

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "disposition": self.disposition.value,
            "run_ids": list(self.run_ids),
            "rationale": self.rationale,
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
        if not isinstance(value, dict) or value.get("schema_version") != 2:
            raise WorkflowError("public QEMU plan must be a schema_version=2 object")
        runs, ladder = value.get("runs"), value.get("ladder")
        if not isinstance(runs, list) or not isinstance(ladder, list):
            raise WorkflowError("public QEMU plan requires runs and ladder lists")
        return cls(
            tuple(PublicRun.from_dict(item) for item in runs),
            tuple(LadderPlan.from_dict(item) for item in ladder),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "runs": [run.to_dict() for run in self.runs],
            "ladder": [item.to_dict() for item in self.ladder],
        }


class PublicQemuService:
    def run(self, project: Project, plan: PublicQemuPlan) -> dict[str, Any]:
        if project.stage(MigrationStage.PUBLIC_QEMU_VALIDATION).status is not StageStatus.RUNNING:
            raise WorkflowError("public_qemu_validation must be RUNNING")
        inputs = {kind.value: self._input(project, kind).to_dict() for kind in PUBLIC_QEMU_INPUTS}
        self._plan_gate(project, plan)
        plan_digest = hashlib.sha256(self._json(plan.to_dict())).hexdigest()
        attempt_dir = project.control / "public-qemu" / plan_digest[:20]
        attempt_dir.mkdir(parents=True, exist_ok=False)
        execute_ids = {
            run_id
            for item in plan.ladder
            if item.disposition is LadderDisposition.EXECUTE
            for run_id in item.run_ids
        }
        executed = {
            result["run_id"]: result
            for result in self.execute_runs(
                project,
                tuple(run for run in plan.runs if run.run_id in execute_ids),
                attempt_dir,
            )
        }
        results = [
            executed.get(run.run_id, self._unexecuted(run, plan.ladder)) for run in plan.runs
        ]
        failed = any(
            result["execution_status"] == ContractExecutionStatus.FAIL.value for result in results
        )
        blocked = any(item.disposition is LadderDisposition.BLOCKED for item in plan.ladder)
        report = {
            "schema_version": 2,
            "inputs": inputs,
            "plan": plan.to_dict(),
            "runs": results,
            "status": StageStatus.FAIL.value if failed else StageStatus.PASS.value,
            "execution_status": (
                ContractExecutionStatus.FAIL.value
                if failed
                else ContractExecutionStatus.BLOCKED.value
                if blocked
                else ContractExecutionStatus.PASS.value
            ),
            "integration_boundary": (
                MigrationBoundary.BLOCKED_FULL_INTEGRATION.value if blocked else None
            ),
            "recorded_at": utc_now(),
        }
        attempt_path = attempt_dir / "attempt.json"
        attempt_path.write_bytes(self._json(report))
        attempt_ref = project.record_artifact(
            MigrationStage.PUBLIC_QEMU_VALIDATION,
            FileArtifact(MigrationArtifact.PUBLIC_QEMU_ATTEMPT, attempt_path),
        )
        if failed:
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

    def execute_runs(
        self, project: Project, runs: tuple[PublicRun, ...], attempt_dir: Path
    ) -> list[dict[str, Any]]:
        return [self._execute_run(project, run, attempt_dir / run.run_id) for run in runs]

    @staticmethod
    def _execute_run(project: Project, run: PublicRun, run_dir: Path) -> dict[str, Any]:
        cwd = workspace_path(project, run.cwd)
        executable = executable_identity(run.command.argv[0], cwd)
        if executable["resolved"] is None:
            raise WorkflowError(f"public QEMU runner is unavailable: {run.command.argv[0]}")
        result = CommandRunner(run_dir).run(
            [str(executable["resolved"]), *run.command.argv[1:]],
            cwd=cwd,
            environment=run.command.environment,
            timeout_seconds=run.command.timeout_seconds,
        )
        passed = PublicQemuService._command_passed(result, run.command)
        return {
            "run_id": run.run_id,
            "contract_ids": list(run.contract_ids),
            "test_ids": list(run.test_ids),
            "oracle": run.oracle,
            "command": asdict(result),
            "evidence_status": (
                ContractEvidenceStatus.VERIFIED.value
                if passed
                else ContractEvidenceStatus.UNKNOWN.value
            ),
            "execution_status": (
                ContractExecutionStatus.PASS.value if passed else ContractExecutionStatus.FAIL.value
            ),
            "attribution": (
                PublicRunAttribution.TARGET_DRIVER_ON_QEMU.value
                if passed
                else PublicRunAttribution.INCONCLUSIVE.value
            ),
        }

    @staticmethod
    def _unexecuted(run: PublicRun, ladder: tuple[LadderPlan, ...]) -> dict[str, Any]:
        dispositions = {item.disposition for item in ladder if run.run_id in item.run_ids}
        status = (
            ContractExecutionStatus.BLOCKED
            if LadderDisposition.BLOCKED in dispositions
            else ContractExecutionStatus.NOT_APPLICABLE
        )
        return {
            "run_id": run.run_id,
            "contract_ids": list(run.contract_ids),
            "test_ids": list(run.test_ids),
            "oracle": run.oracle,
            "command": None,
            "evidence_status": ContractEvidenceStatus.UNKNOWN.value,
            "execution_status": status.value,
            "attribution": PublicRunAttribution.INCONCLUSIVE.value,
        }

    def _plan_gate(self, project: Project, plan: PublicQemuPlan) -> None:
        run_ids = [run.run_id for run in plan.runs]
        if len(run_ids) != len(set(run_ids)):
            raise WorkflowError("public QEMU run IDs must be unique")
        if [item.level for item in plan.ladder] != list(EvidenceLadderLevel):
            raise WorkflowError("public QEMU plan must report the Skill evidence ladder")
        known = set(run_ids)
        for item in plan.ladder:
            if not set(item.run_ids) <= known:
                raise WorkflowError("public QEMU ladder references an unknown run")
            if item.disposition is LadderDisposition.EXECUTE and not item.run_ids:
                raise WorkflowError("executed evidence-ladder level has no run")
        covered_contracts = {item for run in plan.runs for item in run.contract_ids}
        covered_tests = {item for run in plan.runs for item in run.test_ids}
        if covered_contracts != self._expected_contracts(project):
            raise WorkflowError("public QEMU plan does not account for every QEMU contract")
        if covered_tests != self._expected_tests(project):
            raise WorkflowError("public QEMU plan does not account for every retained test")
        self._verify_qemu_evidence(project, plan)

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
    def _verify_qemu_evidence(project: Project, plan: PublicQemuPlan) -> None:
        ref = project.artifact(
            SourceAnalysisStage.SOURCE_CLOSURE,
            SourceAnalysisArtifact.MATERIALS_MANIFEST,
        )
        data = project.artifacts.read(ref)
        knowledge = KnowledgeIndex(
            project.root, CorpusManifest(parse_materials(data), data, ref.digest, ref.source)
        )
        knowledge.status()
        for reference in (reference for run in plan.runs for reference in run.qemu_evidence):
            exact = knowledge.show(str(reference.get("chunk_id")))["result"]
            if (
                exact["domain"] != KnowledgeDomain.QEMU.value
                or reference.get("record_id") != exact["record_id"]
                or file_sha256(knowledge.controlled_path(exact["path"])) != exact["sha256"]
            ):
                raise WorkflowError("public QEMU evidence is not a pinned QEMU original")

    @staticmethod
    def _command_passed(result: CommandResult, command: PlannedCommand) -> bool:
        return (
            result.launched
            and result.launch_error is None
            and not result.timed_out
            and result.exit_code in command.accepted_exit_codes
        )

    @staticmethod
    def _input(project: Project, kind: ArtifactKey):
        dependencies = project.workflow.spec(MigrationStage.PUBLIC_QEMU_VALIDATION).dependencies
        matches = [
            ref
            for stage in dependencies
            for ref in project.current_artifact_refs(stage=stage)
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
    attempt_ref, _ = context.one_auxiliary(MigrationArtifact.PUBLIC_QEMU_ATTEMPT)
    expected_inputs = {
        kind.value: context.one_dependency(kind)[0].to_dict() for kind in PUBLIC_QEMU_INPUTS
    }
    if (
        report.get("schema_version") != 2
        or report.get("inputs") != expected_inputs
        or report.get("attempt_sha256") != attempt_ref.digest
        or report.get("status") != StageStatus.PASS.value
    ):
        raise WorkflowError("public QEMU report is detached from its run")
    PublicQemuPlan.from_dict(report.get("plan"))
