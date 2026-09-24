from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import stat
import subprocess
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from ..acquisition.contracts import AcquisitionArtifact
from ..acquisition.repository import load_repository_acquisition
from ..codex.contracts import CodexArtifact
from ..core.contracts import ArtifactKey
from ..core.ledger import canonical_json
from ..core.models import (
    ActorRole,
    EvaluationMode,
    GeneratedArtifact,
    StageStatus,
    WorkflowError,
    utc_now,
)
from ..core.project import Project
from ..evaluation.contracts import EvaluationArtifact, EvaluationResult
from ..intake.contracts import IntakeArtifact
from ..knowledge.index import file_sha256
from ..migration.contracts import (
    ContractExecutionStatus,
    MigrationArtifact,
    MigrationStage,
)
from ..migration.implementation import validate_worktree_snapshot
from .contracts import (
    CandidateBundleFormat,
    PrivateEvaluationState,
    SealingArtifact,
    SealingEvent,
    SealingStage,
    TimestampReceiptKind,
)


@dataclass(frozen=True, slots=True)
class SealRequest:
    experiment_id: str
    task_id: str
    attempt_id: str
    batch_id: str
    control_plane_version: str
    migration_started_at: str
    public_bundle_sha256: str
    pmc_sha256: str
    candidate_format: CandidateBundleFormat
    migrator_identity: str
    migrator_version: str
    rules_version: str
    prompt_budget: int
    human_interventions: tuple[str, ...]
    private_material_access: bool
    no_cross_task_update: bool

    @classmethod
    def read(cls, path: Path) -> SealRequest:
        value = _read_object(path, "candidate seal request")
        try:
            migrator = value["frozen_migrator"]
            if not isinstance(migrator, dict):
                raise TypeError
            request = cls(
                str(value["experiment_id"]),
                str(value["task_id"]),
                str(value["attempt_id"]),
                str(value["batch_id"]),
                str(value["control_plane_version"]),
                str(value["migration_started_at"]),
                str(value["public_bundle_sha256"]),
                str(value["pmc_sha256"]),
                CandidateBundleFormat(value["candidate_format"]),
                str(migrator["identity"]),
                str(migrator["version"]),
                str(migrator["rules_version"]),
                int(migrator["prompt_budget"]),
                _strings(value["human_interventions"]),
                value["private_material_access"],
                value["no_cross_task_update"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("candidate seal request has an invalid typed boundary") from error
        attempt = PurePosixPath(request.attempt_id)
        if (
            not all(
                (
                    request.experiment_id,
                    request.task_id,
                    request.attempt_id,
                    request.batch_id,
                    request.control_plane_version,
                    request.migration_started_at,
                    request.public_bundle_sha256,
                    request.pmc_sha256,
                    request.migrator_identity,
                    request.migrator_version,
                    request.rules_version,
                )
            )
            or attempt.name != request.attempt_id
            or request.prompt_budget <= 0
            or not isinstance(request.private_material_access, bool)
            or not isinstance(request.no_cross_task_update, bool)
        ):
            raise WorkflowError("candidate seal request is incomplete")
        if request.private_material_access:
            raise WorkflowError(EvaluationResult.NON_INDEPENDENT.value)
        if not request.no_cross_task_update:
            raise WorkflowError("blind candidate requires no-cross-task-update=true")
        return request

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["candidate_format"] = self.candidate_format.value
        value["human_interventions"] = list(self.human_interventions)
        return value


@dataclass(frozen=True, slots=True)
class TimestampReceipt:
    candidate_sha256: str
    kind: TimestampReceiptKind
    authority: str
    receipt_id: str
    issued_at: str
    proof: str

    @classmethod
    def read(cls, path: Path) -> TimestampReceipt:
        value = _read_object(path, "trusted timestamp receipt")
        try:
            receipt = cls(
                str(value["candidate_sha256"]),
                TimestampReceiptKind(value["kind"]),
                str(value["authority"]),
                str(value["receipt_id"]),
                str(value["issued_at"]),
                str(value["proof"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError(
                "trusted timestamp receipt has an invalid typed boundary"
            ) from error
        if not all(asdict(receipt).values()):
            raise WorkflowError("trusted timestamp receipt is incomplete")
        return receipt

    def to_dict(self) -> dict[str, str]:
        value = asdict(self)
        value["kind"] = self.kind.value
        return value


@dataclass(frozen=True, slots=True)
class EvaluatorReceipt:
    candidate_sha256: str
    experiment_id: str
    task_id: str
    attempt_id: str
    receiver_identity: str
    received_at: str
    read_only_location: str
    signer_identity: str
    receipt_id: str
    proof: str
    private_evaluation: PrivateEvaluationState

    @classmethod
    def read(cls, path: Path) -> EvaluatorReceipt:
        value = _read_object(path, "evaluator receipt")
        try:
            receipt = cls(
                str(value["candidate_sha256"]),
                str(value["experiment_id"]),
                str(value["task_id"]),
                str(value["attempt_id"]),
                str(value["receiver_identity"]),
                str(value["received_at"]),
                str(value["read_only_location"]),
                str(value["signer_identity"]),
                str(value["receipt_id"]),
                str(value["proof"]),
                PrivateEvaluationState(value["private_evaluation"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("evaluator receipt has an invalid typed boundary") from error
        if not all(asdict(receipt).values()):
            raise WorkflowError("evaluator receipt is incomplete")
        return receipt

    def to_dict(self) -> dict[str, str]:
        value = asdict(self)
        value["private_evaluation"] = self.private_evaluation.value
        return value


@dataclass(frozen=True, slots=True)
class CandidateSeal:
    digest: str
    manifest: dict[str, object]
    bundle_path: Path
    transfer_path: Path


class CandidateSealer:
    REQUIRED_ARTIFACTS: ClassVar[tuple[ArtifactKey, ...]] = (
        IntakeArtifact.IDENTITY_RECORD,
        AcquisitionArtifact.REVISION_MANIFEST,
        EvaluationArtifact.PUBLIC_BUNDLE,
        EvaluationArtifact.CURATOR_COMMITMENT,
        MigrationArtifact.CONTRACTS,
        MigrationArtifact.TEST_PORT_MATRIX,
        MigrationArtifact.IMPLEMENTATION_BUNDLE,
        MigrationArtifact.COMPLIANCE_REPORT,
        MigrationArtifact.RUNTIME_ARTIFACT,
        MigrationArtifact.ARTIFACT_IDENTITY,
        MigrationArtifact.FINAL_EVIDENCE_REVIEW_REPORT,
    )
    PRIVATE_ARTIFACTS = frozenset({EvaluationArtifact.PRIVATE_ASSERTIONS.value})

    def seal(
        self,
        project: Project,
        *,
        request_path: Path,
        receipt_path: Path,
        output: Path,
    ) -> CandidateSeal:
        request, receipt, occurrences, attempt_dir, transfer_dir = self._prepare(
            project, request_path, receipt_path, output
        )
        entities = self._entities(project, occurrences, request)
        entity_manifest = self._entity_manifest(project, request, entities)
        bundle = self._archive({"manifest.json": _json(entity_manifest), **entities})
        digest = hashlib.sha256(bundle).hexdigest()
        if receipt.candidate_sha256 != digest:
            raise WorkflowError("trusted timestamp receipt does not bind this candidate digest")
        if receipt.authority == request.migrator_identity:
            raise WorkflowError("migrator self-signature is not an external timestamp")

        bundle_path = attempt_dir / "candidate.tar"
        bundle_path.write_bytes(bundle)
        previous = self._ledger(project)[-1]["event_hash"]
        event_payload = {
            "experiment_id": request.experiment_id,
            "task_id": request.task_id,
            "attempt_id": request.attempt_id,
            "batch_id": request.batch_id,
            "previous_digest": previous,
            "migration_started_at": request.migration_started_at,
            "public_task_sha256": request.public_bundle_sha256,
            "candidate_sha256": digest,
        }
        event_hash = project.record_event(SealingEvent.CANDIDATE_SEALED, event_payload)
        ledger_event = {**event_payload, "event_hash": event_hash}
        transfer = {
            "schema_version": 1,
            "candidate_sha256": digest,
            "experiment_id": request.experiment_id,
            "task_id": request.task_id,
            "attempt_id": request.attempt_id,
            "destination": str(transfer_dir),
            "transferred_at": utc_now(),
            "read_only": True,
            "private_evaluation": "NOT_RUN_BY_MIGRATOR",
        }
        manifest: dict[str, object] = {
            "schema_version": 2,
            "candidate_sha256": digest,
            "bundle_size": len(bundle),
            "entity_manifest_sha256": hashlib.sha256(_json(entity_manifest)).hexdigest(),
            "request": request.to_dict(),
            "ledger_event": ledger_event,
            "timestamp_receipt": receipt.to_dict(),
            "transfer": transfer,
            "private_evaluation": "NOT_RUN_BY_MIGRATOR",
        }
        sidecars = {
            "candidate.tar": bundle,
            "candidate-manifest.json": _json(manifest),
            "candidate-ledger-event.json": _json(ledger_event),
            "timestamp-receipt.json": _json(receipt.to_dict()),
            "transfer-record.json": _json(transfer),
        }
        self._write_read_only(sidecars, bundle_path, attempt_dir, transfer_dir)

        project.finalize_stage(
            SealingStage.CANDIDATE_SEALING,
            (
                GeneratedArtifact(
                    SealingArtifact.CANDIDATE_MANIFEST,
                    _json(manifest),
                    f"generated:candidate-manifest:{digest}",
                ),
                GeneratedArtifact(
                    SealingArtifact.CANDIDATE_BUNDLE,
                    bundle,
                    f"generated:candidate-bundle:{digest}",
                ),
                GeneratedArtifact(
                    SealingArtifact.CANDIDATE_LEDGER_EVENT,
                    _json(ledger_event),
                    f"generated:candidate-ledger:{event_hash}",
                ),
                GeneratedArtifact(
                    SealingArtifact.CANDIDATE_TIMESTAMP_RECEIPT,
                    _json(receipt.to_dict()),
                    f"external:timestamp:{receipt.receipt_id}",
                ),
                GeneratedArtifact(
                    SealingArtifact.CANDIDATE_TRANSFER_RECORD,
                    _json(transfer),
                    f"generated:candidate-transfer:{digest}",
                ),
            ),
        )
        return CandidateSeal(digest, manifest, bundle_path, transfer_dir)

    def transfer(self, project: Project, *, receipt_path: Path) -> dict[str, Any]:
        project.ensure_role(ActorRole.MIGRATION_OPERATOR)
        if project.config.evaluation_mode is not EvaluationMode.PROSPECTIVE_BLIND:
            raise WorkflowError("candidate transfer requires prospective blind mode")
        project.verify_integrity()
        receipt = EvaluatorReceipt.read(_project_file(project, receipt_path))
        manifest = project.load_json_artifact(
            SealingStage.CANDIDATE_SEALING, SealingArtifact.CANDIDATE_MANIFEST
        )
        sealed_transfer = project.load_json_artifact(
            SealingStage.CANDIDATE_SEALING, SealingArtifact.CANDIDATE_TRANSFER_RECORD
        )
        _bind_evaluator_receipt(receipt, manifest, sealed_transfer)
        _verify_exchange(project, sealed_transfer)

        stage = project.stage(SealingStage.CANDIDATE_TRANSFER)
        receipt_data = _json({"schema_version": 1, **receipt.to_dict()})
        if stage.status is StageStatus.PASS:
            frozen = project.artifacts.read(
                project.artifact(SealingStage.CANDIDATE_TRANSFER, SealingArtifact.EVALUATOR_RECEIPT)
            )
            if frozen != receipt_data:
                raise WorkflowError("candidate transfer already has a different receipt")
            return project.load_json_artifact(
                SealingStage.CANDIDATE_TRANSFER, SealingArtifact.CANDIDATE_TRANSFER_RECORD
            )
        if stage.status is StageStatus.READY:
            project.start(SealingStage.CANDIDATE_TRANSFER)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(f"candidate_transfer is {stage.status.value}, not READY/RUNNING")

        seal_event = manifest["ledger_event"]
        previous = self._ledger(project)[-1]["event_hash"]
        payload = {
            "candidate_sha256": receipt.candidate_sha256,
            "experiment_id": receipt.experiment_id,
            "task_id": receipt.task_id,
            "attempt_id": receipt.attempt_id,
            "receiver_identity": receipt.receiver_identity,
            "received_at": receipt.received_at,
            "receipt_id": receipt.receipt_id,
            "receipt_sha256": hashlib.sha256(receipt_data).hexdigest(),
            "candidate_seal_event": seal_event["event_hash"],
            "previous_digest": previous,
            "private_evaluation": receipt.private_evaluation.value,
        }
        event_hash = project.record_event(SealingEvent.CANDIDATE_TRANSFERRED, payload)
        record = {
            "schema_version": 1,
            **payload,
            "event_hash": event_hash,
            "read_only_location": receipt.read_only_location,
            "signer_identity": receipt.signer_identity,
        }
        project.finalize_stage(
            SealingStage.CANDIDATE_TRANSFER,
            (
                GeneratedArtifact(
                    SealingArtifact.EVALUATOR_RECEIPT,
                    receipt_data,
                    f"external:evaluator-receipt:{receipt.receipt_id}",
                ),
                GeneratedArtifact(
                    SealingArtifact.CANDIDATE_TRANSFER_RECORD,
                    _json(record),
                    f"generated:candidate-transfer:{receipt.candidate_sha256}",
                ),
            ),
        )
        return record

    def _prepare(
        self,
        project: Project,
        request_path: Path,
        receipt_path: Path,
        output: Path,
    ) -> tuple[SealRequest, TimestampReceipt, list[dict[str, Any]], Path, Path]:
        project.ensure_role(ActorRole.MIGRATION_OPERATOR)
        if project.config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE:
            raise WorkflowError("developer evidence cannot claim blind candidate chronology")
        project.verify_integrity()
        request = SealRequest.read(_project_file(project, request_path))
        receipt = TimestampReceipt.read(_project_file(project, receipt_path))
        stage = project.stage(SealingStage.CANDIDATE_SEALING)
        if stage.status is StageStatus.READY:
            project.start(SealingStage.CANDIDATE_SEALING)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(f"candidate_sealing is {stage.status.value}, not READY/RUNNING")
        occurrences = self._occurrences(project)
        kinds = {item["kind"] for item in occurrences}
        if kinds & self.PRIVATE_ARTIFACTS:
            raise WorkflowError(EvaluationResult.NON_INDEPENDENT.value)
        missing = sorted(
            artifact.value for artifact in self.REQUIRED_ARTIFACTS if artifact.value not in kinds
        )
        if missing:
            raise WorkflowError(
                "candidate cannot be sealed; missing public artifacts: " + ", ".join(missing)
            )
        if (
            request.public_bundle_sha256
            != self._one(occurrences, EvaluationArtifact.PUBLIC_BUNDLE)["digest"]
            or request.pmc_sha256 != self._one(occurrences, MigrationArtifact.CONTRACTS)["digest"]
        ):
            raise WorkflowError("candidate request differs from the frozen public task or PMC")
        attempt_dir = project.control / "candidates" / request.attempt_id
        transfer_dir = output.resolve()
        if attempt_dir.exists() or transfer_dir.exists():
            raise WorkflowError("candidate attempt and transfer destination must be fresh")
        attempt_dir.mkdir(parents=True)
        return request, receipt, occurrences, attempt_dir, transfer_dir

    @staticmethod
    def _write_read_only(sidecars: dict[str, bytes], bundle_path: Path, *roots: Path) -> None:
        for root in roots:
            root.mkdir(parents=True, exist_ok=root == bundle_path.parent)
            for name, data in sidecars.items():
                path = root / name
                if path.exists() and path != bundle_path:
                    raise WorkflowError("candidate transfer would overwrite an existing entity")
                if path != bundle_path:
                    path.write_bytes(data)
                path.chmod(0o444)
            root.chmod(0o555)

    def _entities(
        self,
        project: Project,
        occurrences: list[dict[str, Any]],
        request: SealRequest,
    ) -> dict[str, bytes]:
        entities: dict[str, bytes] = {}
        public_documents = []
        for item in occurrences:
            data = project.artifacts.path_for_digest(item["digest"]).read_bytes()
            if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["digest"]:
                raise WorkflowError("candidate artifact entity changed before sealing")
            path = (
                f"artifacts/{item['position']:03d}/{item['kind']}/"
                f"{item['direction'][0]}-{item['ordinal']:04d}-{item['digest']}.bin"
            )
            entities[path] = data
            if item["kind"] in {
                MigrationArtifact.PUBLIC_QEMU_ATTEMPT.value,
                MigrationArtifact.PUBLIC_QEMU_REPORT.value,
                MigrationArtifact.FINAL_EVIDENCE_REVIEW_REPORT.value,
            }:
                public_documents.append(json.loads(data))

        implementation = self._latest_implementation(project, occurrences)
        worktree = validate_worktree_snapshot(project.root, implementation)
        for item in implementation["files"]:
            if item["state"] == "deleted":
                continue
            path = (worktree / str(item["path"])).resolve()
            if (
                worktree not in path.parents
                or not path.is_file()
                or file_sha256(path) != item["sha256"]
            ):
                raise WorkflowError("candidate source differs from the current implementation")
            content = path.read_bytes()
            entities[f"candidate-source/{item['path']}"] = content

        patch = subprocess.run(
            ["git", "diff", "--binary", "--no-ext-diff", implementation["target_worktree"]["base_commit"],
             "--", ".", ":(exclude).dpf-output"],
            cwd=worktree,
            check=True,
            capture_output=True,
        ).stdout
        entities["target/changes.patch"] = patch
        entities["provenance/ledger-before-seal.json"] = _json(
            {
                "events": self._ledger(project),
                "human_interventions": list(request.human_interventions),
            }
        )
        entities["pmc/capability-map.json"] = _json(self._capability_map(project, public_documents))
        self._public_run_files(project, public_documents, entities)
        self._runtime(project, occurrences, public_documents, entities)
        self._rules(project, entities)
        return entities

    @staticmethod
    def _entity_manifest(
        project: Project, request: SealRequest, entities: dict[str, bytes]
    ) -> dict[str, Any]:
        records = [
            {"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for path, data in sorted(entities.items())
        ]
        prompt_hashes = [
            item["sha256"] for item in records if CodexArtifact.PROMPT.value in item["path"]
        ]
        rule_hashes = [item["sha256"] for item in records if item["path"].startswith("rules/")]
        return {
            "schema_version": 1,
            "project": project.config.to_dict(),
            "blind_binding": request.to_dict(),
            "prompt_set_sha256": _digest_list(prompt_hashes),
            "rule_set_sha256": _digest_list(rule_hashes),
            "entities": records,
            "private_evaluation": "NOT_RUN_BY_MIGRATOR",
        }

    @staticmethod
    def _archive(entities: dict[str, bytes]) -> bytes:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for path, data in sorted(entities.items()):
                info = tarfile.TarInfo(path)
                info.size = len(data)
                info.mode = 0o444
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(data))
        return output.getvalue()

    @staticmethod
    def _occurrences(project: Project) -> list[dict[str, Any]]:
        connection = sqlite3.connect(project.database_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT st.position, sa.stage_name, sa.direction, sa.ordinal,
                       sa.digest, sa.kind, ac.size
                FROM stage_artifacts sa
                JOIN artifact_contents ac
                  ON ac.digest = sa.digest AND ac.kind = sa.kind
                JOIN stages st ON st.name = sa.stage_name
                WHERE st.position < (SELECT position FROM stages WHERE name = ?)
                ORDER BY st.position, sa.direction, sa.ordinal
                """,
                (SealingStage.CANDIDATE_SEALING.value,),
            ).fetchall()
            return [
                {
                    "position": row["position"],
                    "stage": row["stage_name"],
                    "direction": row["direction"],
                    "ordinal": row["ordinal"],
                    "digest": row["digest"],
                    "kind": row["kind"],
                    "size": row["size"],
                }
                for row in rows
            ]
        finally:
            connection.close()

    @staticmethod
    def _ledger(project: Project) -> list[dict[str, Any]]:
        connection = sqlite3.connect(project.database_path)
        connection.row_factory = sqlite3.Row
        try:
            return [
                {
                    "sequence": row["sequence"],
                    "created_at": row["created_at"],
                    "event_type": row["event_type"],
                    "payload": json.loads(row["payload"]),
                    "previous_hash": row["previous_hash"],
                    "event_hash": row["event_hash"],
                }
                for row in connection.execute("SELECT * FROM events ORDER BY sequence")
            ]
        finally:
            connection.close()

    @staticmethod
    def _one(occurrences: list[dict[str, Any]], kind: ArtifactKey) -> dict[str, Any]:
        matches = [item for item in occurrences if item["kind"] == kind.value]
        if len(matches) != 1:
            raise WorkflowError(f"candidate requires exactly one {kind.value}")
        return matches[0]

    @staticmethod
    def _latest_implementation(
        project: Project, occurrences: list[dict[str, Any]]
    ) -> dict[str, Any]:
        original = CandidateSealer._one(occurrences, MigrationArtifact.IMPLEMENTATION_BUNDLE)
        return json.loads(project.artifacts.path_for_digest(original["digest"]).read_bytes())

    @staticmethod
    def _worktree(project: Project) -> Path:
        acquisition = load_repository_acquisition(project)
        return (project.root / acquisition.target_worktree.path).resolve()

    @staticmethod
    def _public_run_files(
        project: Project,
        documents: list[dict[str, Any]],
        entities: dict[str, bytes],
    ) -> None:
        worktree = CandidateSealer._worktree(project)
        for document_index, document in enumerate(documents):
            for run_index, run in enumerate(document.get("runs", [])):
                files = [(worktree / item["path"], item["sha256"])
                         for item in run.get("logs", [])]
                command = run.get("command", {})
                for name in ("stdout", "stderr"):
                    if command.get(f"{name}_path"):
                        files.append((Path(command[f"{name}_path"]),
                                      command[f"{name}_sha256"]))
                trace = run.get("exec_trace", {})
                if trace.get("path"):
                    files.append((project.root / trace["path"], trace["sha256"]))
                if trace.get("container_evidence"):
                    files.append((Path(trace["container_evidence"]),
                                  trace["container_evidence_sha256"]))
                for index, (path, digest) in enumerate(files):
                    path = path.resolve()
                    if (project.root not in path.parents or not path.is_file()
                            or file_sha256(path) != digest):
                        raise WorkflowError("public run evidence changed before candidate sealing")
                    entities[f"public-runs/{document_index:04d}-{run_index:04d}/{index:04d}.bin"] = path.read_bytes()

    @staticmethod
    def _runtime(
        project: Project,
        occurrences: list[dict[str, Any]],
        documents: list[dict[str, Any]],
        entities: dict[str, bytes],
    ) -> None:
        digest = CandidateSealer._one(occurrences, MigrationArtifact.RUNTIME_ARTIFACT)["digest"]
        path = project.artifacts.path_for_digest(digest)
        if not path.is_file() or file_sha256(path) != digest:
            raise WorkflowError("current runtime artifact is absent or stale")
        entities[f"runtime/{digest}.bin"] = path.read_bytes()

    @staticmethod
    def _rules(project: Project, entities: dict[str, bytes]) -> None:
        if not project.config.skill_root:
            raise WorkflowError("blind candidate has no frozen Skill rules")
        root = Path(project.config.skill_root).resolve() / "knowledge-guided-driver-port"
        files = sorted(path for path in root.rglob("*") if path.is_file())
        if not files:
            raise WorkflowError("blind candidate Skill rules are absent")
        for path in files:
            entities[f"rules/{path.relative_to(root).as_posix()}"] = path.read_bytes()

    @staticmethod
    def _capability_map(project: Project, public_documents: list[dict[str, Any]]) -> dict[str, Any]:
        # Contracts/tests are an authored Markdown plan, not a model-filled JSON table.
        # Preserve the evidence without inventing per-contract execution classifications.
        plan = project.artifact(MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS)
        tests = project.artifact(MigrationStage.CONTRACTS, MigrationArtifact.TEST_PORT_MATRIX)
        return {
            "schema_version": 2,
            "migration_plan": plan.to_dict(),
            "test_plan": tests.to_dict(),
            "classification": "REQUIRES_INDEPENDENT_EVALUATION",
            "public_evidence": public_documents,
        }


def _project_file(project: Project, path: Path) -> Path:
    resolved = path.resolve()
    if project.root not in resolved.parents or not resolved.is_file():
        raise WorkflowError("candidate seal inputs must be regular project files")
    return resolved


def _bind_evaluator_receipt(
    receipt: EvaluatorReceipt,
    manifest: dict[str, Any],
    sealed_transfer: dict[str, Any],
) -> None:
    request = manifest.get("request", {})
    if (
        receipt.candidate_sha256 != manifest.get("candidate_sha256")
        or receipt.experiment_id != request.get("experiment_id")
        or receipt.task_id != request.get("task_id")
        or receipt.attempt_id != request.get("attempt_id")
        or Path(receipt.read_only_location).resolve()
        != Path(str(sealed_transfer.get("destination", ""))).resolve()
        or receipt.signer_identity == request.get("migrator_identity")
    ):
        raise WorkflowError("evaluator receipt does not bind the sealed candidate transfer")


def _verify_exchange(project: Project, sealed_transfer: dict[str, Any]) -> None:
    root = Path(str(sealed_transfer.get("destination", ""))).resolve()
    artifacts = {
        "candidate.tar": SealingArtifact.CANDIDATE_BUNDLE,
        "candidate-manifest.json": SealingArtifact.CANDIDATE_MANIFEST,
        "candidate-ledger-event.json": SealingArtifact.CANDIDATE_LEDGER_EVENT,
        "timestamp-receipt.json": SealingArtifact.CANDIDATE_TIMESTAMP_RECEIPT,
        "transfer-record.json": SealingArtifact.CANDIDATE_TRANSFER_RECORD,
    }
    if (
        not root.is_dir()
        or stat.S_IMODE(root.stat().st_mode) != 0o555
        or any(
            not (path := root / name).is_file()
            or stat.S_IMODE(path.stat().st_mode) != 0o444
            or path.read_bytes()
            != project.artifacts.read(project.artifact(SealingStage.CANDIDATE_SEALING, artifact))
            for name, artifact in artifacts.items()
        )
    ):
        raise WorkflowError("sealed candidate exchange changed before evaluator receipt")


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise WorkflowError(f"{label} must be a schema_version=1 object")
    return value


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise TypeError
    return tuple(value)


def _json(value: Any) -> bytes:
    return (canonical_json(value) + "\n").encode()


def _digest_list(values: list[str]) -> str:
    return hashlib.sha256(_json(sorted(values))).hexdigest()
