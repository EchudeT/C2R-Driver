from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from ..acquisition.repository import load_repository_acquisition
from ..core.execution import CommandRunner
from ..core.models import FileArtifact, GeneratedArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..environment.evidence import executable_identity, workspace_path
from ..knowledge.index import KnowledgeIndex, file_sha256
from .artifact_preparation import PlannedCommand
from .contracts import (
    ContractExecutionStatus,
    EvidenceLadderLevel,
    ImplementationFileRole,
    MigrationArtifact,
    MigrationStage,
    PublicRunAttribution,
    RepairAction,
    RepairAttribution,
)
from .public_qemu import PublicQemuPlan, PublicQemuService


@dataclass(frozen=True, slots=True)
class RepairPath:
    path: str
    role: ImplementationFileRole

    @classmethod
    def from_dict(cls, value: Any) -> RepairPath:
        try:
            result = cls(str(value["path"]), ImplementationFileRole(value["role"]))
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("repair path has an invalid typed boundary") from error
        relative = PurePosixPath(result.path)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != result.path:
            raise WorkflowError("repair path must be canonical and relative")
        return result

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "role": self.role.value}


@dataclass(frozen=True, slots=True)
class PublicRepairPlan:
    attribution: RepairAttribution
    action: RepairAction
    failure_run_ids: tuple[str, ...]
    contract_ids: tuple[str, ...]
    test_ids: tuple[str, ...]
    changed_paths: tuple[RepairPath, ...]
    patch: str
    evidence: tuple[dict[str, Any], ...]
    rationale: str
    temporary_diagnostics_removed: bool

    @classmethod
    def read(cls, path: Path) -> PublicRepairPlan:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("Codex public repair response is not UTF-8 JSON") from error
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Any) -> PublicRepairPlan:
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise WorkflowError("public repair response must be a schema_version=1 object")
        try:
            evidence = value["evidence"]
            if not isinstance(evidence, list) or not all(
                isinstance(item, dict) for item in evidence
            ):
                raise TypeError
            plan = cls(
                RepairAttribution(value["attribution"]),
                RepairAction(value["action"]),
                _strings(value["failure_run_ids"]),
                _strings(value["contract_ids"]),
                _strings(value["test_ids"]),
                tuple(RepairPath.from_dict(item) for item in value["changed_paths"]),
                str(value["patch"]),
                tuple(evidence),
                str(value["rationale"]),
                value["temporary_diagnostics_removed"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("public repair response has an invalid typed boundary") from error
        if (
            not isinstance(plan.temporary_diagnostics_removed, bool)
            or not plan.rationale.strip()
            or not plan.evidence
            or (plan.action is RepairAction.APPLY) != bool(plan.patch.strip())
            or (plan.action is RepairAction.APPLY) != bool(plan.changed_paths)
            or (plan.action is RepairAction.APPLY and not plan.attribution.writable)
            or (plan.action is RepairAction.APPLY and not (plan.contract_ids or plan.test_ids))
            or (plan.action is RepairAction.APPLY and not plan.temporary_diagnostics_removed)
            or (not plan.failure_run_ids)
        ):
            raise WorkflowError("public repair response is incomplete or unsafe")
        return plan

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "attribution": self.attribution.value,
            "action": self.action.value,
            "failure_run_ids": list(self.failure_run_ids),
            "contract_ids": list(self.contract_ids),
            "test_ids": list(self.test_ids),
            "changed_paths": [item.to_dict() for item in self.changed_paths],
            "patch": self.patch,
            "evidence": list(self.evidence),
            "rationale": self.rationale,
            "temporary_diagnostics_removed": self.temporary_diagnostics_removed,
        }


class PublicRepairService:
    def prepare(self, project: Project) -> tuple[dict[str, Any], dict[str, Any]] | None:
        project.verify_integrity()
        public_stage = project.stage(MigrationStage.PUBLIC_QEMU_VALIDATION)
        if public_stage.status is StageStatus.PASS:
            return None
        if public_stage.status is StageStatus.RUNNING:
            ref, failure = self._latest(project, MigrationStage.PUBLIC_QEMU_VALIDATION)
            if failure.get("status") != StageStatus.FAIL.value:
                raise WorkflowError("public repair requires a frozen failed public QEMU attempt")
            project.complete(MigrationStage.PUBLIC_QEMU_VALIDATION, StageStatus.FAIL)
        elif public_stage.status is StageStatus.FAIL:
            ref, failure = self._latest(project, MigrationStage.PUBLIC_QEMU_VALIDATION)
        else:
            raise WorkflowError("public QEMU validation has no repairable terminal result")

        prior = self._optional_latest(project, MigrationStage.PUBLIC_REPAIR)
        if prior is not None:
            ref, failure = prior
        return ref.to_dict(), failure

    def finalize_not_applicable(self, project: Project) -> dict[str, Any]:
        if project.stage(MigrationStage.PUBLIC_REPAIR).status is StageStatus.READY:
            project.start(MigrationStage.PUBLIC_REPAIR)
        report = {
            "schema_version": 1,
            "outcome": ContractExecutionStatus.NOT_APPLICABLE.value,
            "reason": "The complete public QEMU evidence ladder has no failed run to repair.",
            "changed_paths": [],
            "recorded_at": utc_now(),
        }
        self._finalize(project, report)
        return report

    def run(
        self,
        project: Project,
        plan: PublicRepairPlan,
        source_ref: dict[str, Any],
        failure: dict[str, Any],
    ) -> dict[str, Any]:
        if project.stage(MigrationStage.PUBLIC_REPAIR).status is not StageStatus.RUNNING:
            raise WorkflowError("public_repair must be RUNNING")
        self._boundary_gate(project, plan, failure)
        attempt_id = hashlib.sha256(
            self._json({"source": source_ref, "plan": plan.to_dict()})
        ).hexdigest()[:20]
        attempt_dir = project.control / "public-repair" / attempt_id
        attempt_dir.mkdir(parents=True, exist_ok=False)
        before = self._implementation(project, failure)
        if plan.action is RepairAction.BLOCKED:
            report = {
                "schema_version": 1,
                "status": StageStatus.PASS.value,
                "outcome": ContractExecutionStatus.BLOCKED.value,
                "source_attempt": source_ref,
                "plan": plan.to_dict(),
                "before_implementation_sha256": before["digest"],
                "after_implementation_sha256": before["digest"],
                "changed_paths": [],
                "runs": [],
                "recorded_at": utc_now(),
            }
            return self._record_and_finalize(project, report, attempt_dir)

        changed = self._apply(project, plan, before)
        implementation = self._derive_implementation(project, before, changed)
        try:
            artifact = self._rebuild(project, failure, implementation, attempt_dir)
            rerun_plan, required = self._rerun_plan(project, failure, plan, artifact)
            runs = PublicQemuService().execute_runs(project, rerun_plan.runs, attempt_dir / "qemu")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, WorkflowError) as error:
            report = {
                "schema_version": 1,
                "status": StageStatus.FAIL.value,
                "outcome": ContractExecutionStatus.FAIL.value,
                "source_attempt": source_ref,
                "plan": plan.to_dict(),
                "before_implementation_sha256": before["digest"],
                "after_implementation_sha256": implementation["digest"],
                "implementation": implementation,
                "changed_paths": changed,
                "runs": [],
                "error": str(error),
                "recorded_at": utc_now(),
            }
            return self._record_and_finalize(project, report, attempt_dir)
        passed = all(run["execution_status"] == ContractExecutionStatus.PASS.value for run in runs)
        report = {
            "schema_version": 1,
            "status": StageStatus.PASS.value if passed else StageStatus.FAIL.value,
            "outcome": ContractExecutionStatus.PASS.value
            if passed
            else ContractExecutionStatus.FAIL.value,
            "source_attempt": source_ref,
            "plan": plan.to_dict(),
            "before_implementation_sha256": before["digest"],
            "after_implementation_sha256": implementation["digest"],
            "implementation": implementation,
            "artifact_identity": artifact,
            "rerun_plan": rerun_plan.to_dict(),
            "required_run_ids": required,
            "runs": runs,
            "changed_paths": changed,
            "recorded_at": utc_now(),
        }
        return self._record_and_finalize(project, report, attempt_dir)

    def _boundary_gate(
        self, project: Project, plan: PublicRepairPlan, failure: dict[str, Any]
    ) -> None:
        runs = {str(item["run_id"]): item for item in failure.get("runs", [])}
        failed = {
            run_id
            for run_id, result in runs.items()
            if result.get("execution_status") == ContractExecutionStatus.FAIL.value
        }
        if not set(plan.failure_run_ids) <= failed:
            raise WorkflowError("repair plan is not bound to failed public runs")
        bound_contracts = {
            item for run_id in plan.failure_run_ids for item in runs[run_id]["contract_ids"]
        }
        bound_tests = {item for run_id in plan.failure_run_ids for item in runs[run_id]["test_ids"]}
        if not set(plan.contract_ids) <= bound_contracts or not set(plan.test_ids) <= bound_tests:
            raise WorkflowError("repair plan lacks failed contract/test binding")
        source_attributions = {
            PublicRunAttribution(runs[item]["attribution"]) for item in plan.failure_run_ids
        }
        allowed = {
            PublicRunAttribution.TARGET_DRIVER_ON_QEMU: {
                RepairAttribution.DRIVER_TRANSLATION,
                RepairAttribution.SOURCE_ASSUMPTION,
                RepairAttribution.TARGET_API_PLATFORM,
                RepairAttribution.INCONCLUSIVE,
            },
            PublicRunAttribution.PUBLIC_HARNESS: {
                RepairAttribution.ADAPTED_TEST,
                RepairAttribution.HARNESS_PACKAGING,
                RepairAttribution.INCONCLUSIVE,
            },
            PublicRunAttribution.ENVIRONMENT: {
                RepairAttribution.QEMU_MODEL,
                RepairAttribution.ENVIRONMENT_TOOLING,
                RepairAttribution.INCONCLUSIVE,
            },
            PublicRunAttribution.INCONCLUSIVE: {RepairAttribution.INCONCLUSIVE},
        }
        if any(plan.attribution not in allowed[item] for item in source_attributions):
            raise WorkflowError("repair attribution contradicts the frozen public evidence")

        bundle = project.load_json_artifact(
            MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE
        )
        roles = {
            str(item["path"]): ImplementationFileRole(item["role"]) for item in bundle["files"]
        }
        permitted = {
            RepairAttribution.DRIVER_TRANSLATION: {ImplementationFileRole.DRIVER},
            RepairAttribution.SOURCE_ASSUMPTION: {ImplementationFileRole.DRIVER},
            RepairAttribution.ADAPTED_TEST: {ImplementationFileRole.PUBLIC_TEST},
            RepairAttribution.HARNESS_PACKAGING: {
                ImplementationFileRole.PUBLIC_TEST,
                ImplementationFileRole.INTEGRATION,
            },
            RepairAttribution.TARGET_API_PLATFORM: {ImplementationFileRole.INTEGRATION},
        }.get(plan.attribution, set())
        if any(
            roles.get(item.path) is not item.role or item.role not in permitted
            for item in plan.changed_paths
        ):
            raise WorkflowError("repair patch escapes its attributed writable boundary")
        self._evidence(project, plan.evidence)

    def _apply(
        self, project: Project, plan: PublicRepairPlan, implementation: dict[str, Any]
    ) -> list[dict[str, str]]:
        worktree = self._worktree(project)
        patch = plan.patch.encode("utf-8")
        paths = self._patch_paths(worktree, patch)
        declared = {item.path for item in plan.changed_paths}
        if paths != declared:
            raise WorkflowError("declared repair paths differ from the patch")
        before = {path: file_sha256(worktree / path) for path in declared}
        checked = subprocess.run(
            ["git", "apply", "--check", "-"],
            cwd=worktree,
            input=patch,
            capture_output=True,
            check=False,
        )
        if checked.returncode:
            raise WorkflowError("repair patch does not apply to the frozen implementation")
        applied = subprocess.run(
            ["git", "apply", "-"],
            cwd=worktree,
            input=patch,
            capture_output=True,
            check=False,
        )
        if applied.returncode:
            raise WorkflowError("repair patch application failed")
        after = {path: file_sha256(worktree / path) for path in declared}
        if before == after:
            raise WorkflowError("repair patch did not change the declared files")
        return [
            {
                "path": item.path,
                "role": item.role.value,
                "before_sha256": before[item.path],
                "sha256": after[item.path],
            }
            for item in plan.changed_paths
        ]

    @staticmethod
    def _patch_paths(worktree: Path, patch: bytes) -> set[str]:
        result = subprocess.run(
            ["git", "apply", "--numstat", "-"],
            cwd=worktree,
            input=patch,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise WorkflowError("repair response is not a supported unified patch")
        paths = set()
        for line in result.stdout.decode("utf-8").splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3 or parts[0] == "-" or parts[1] == "-":
                raise WorkflowError("binary or malformed repair patches are not supported")
            relative = PurePosixPath(parts[2])
            if relative.is_absolute() or ".." in relative.parts:
                raise WorkflowError("repair patch path escapes the target worktree")
            paths.add(relative.as_posix())
        return paths

    def _derive_implementation(
        self, project: Project, previous: dict[str, Any], changed: list[dict[str, str]]
    ) -> dict[str, Any]:
        worktree = self._worktree(project)
        document = dict(previous["document"])
        changed_by_path = {item["path"]: item for item in changed}
        files = []
        for item in document["files"]:
            path = worktree / item["path"]
            current = dict(item)
            current["content"] = path.read_text(encoding="utf-8")
            current["sha256"] = file_sha256(path)
            files.append(current)
        document["files"] = files
        document["repair_parent_sha256"] = previous["digest"]
        data = self._json(document)
        identity = project.artifacts.put_bytes(
            data, kind=MigrationArtifact.IMPLEMENTATION_BUNDLE.value
        )
        if identity.digest == previous["digest"] or set(changed_by_path) == set():
            raise WorkflowError("repair did not create a new implementation identity")
        return {"digest": identity.digest, "document": document, "content": asdict(identity)}

    def _rebuild(
        self,
        project: Project,
        failure: dict[str, Any],
        implementation: dict[str, Any],
        attempt_dir: Path,
    ) -> dict[str, Any]:
        previous = self._artifact_identity(project, failure)
        plan = previous["plan"]
        artifact_dir = attempt_dir / "artifact"
        artifact_dir.mkdir()
        outputs = {
            "final_artifact": artifact_dir / "runtime.bin",
            "packaged_test_artifact": artifact_dir / "public-tests.pkg",
            "presence_manifest": artifact_dir / "presence.json",
        }
        payloads = [
            {"path": item["path"], "role": item["role"], "sha256": item["sha256"]}
            for item in implementation["document"]["files"]
        ]
        payload_path = artifact_dir / "payloads.json"
        payload_path.write_bytes(
            self._json(
                {"implementation_bundle_sha256": implementation["digest"], "payloads": payloads}
            )
        )
        replacements = {
            str((project.root / plan[name]).resolve()): str(path.resolve())
            for name, path in outputs.items()
        }
        for candidate in plan["tool_evidence_paths"]:
            path = workspace_path(project, candidate)
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (
                isinstance(value, dict)
                and {"implementation_bundle_sha256", "payloads"} <= value.keys()
            ):
                replacements[str(path)] = str(payload_path)
        cwd = self._worktree(project)
        runner = CommandRunner(attempt_dir / "artifact-commands")
        results = {}
        for name in ("build", "inspect"):
            command = PlannedCommand.from_dict(plan[name])
            argv = [replacements.get(str(Path(item).resolve()), item) for item in command.argv]
            tool = executable_identity(argv[0], cwd)
            result = runner.run(
                [str(tool["resolved"]), *argv[1:]],
                cwd=cwd,
                environment=command.environment,
                timeout_seconds=command.timeout_seconds,
            )
            if not PublicQemuService._command_passed(result, command):
                raise WorkflowError(f"repair artifact {name} command failed")
            results[name] = asdict(result)
        runtime = outputs["final_artifact"]
        packaged = outputs["packaged_test_artifact"]
        presence = json.loads(outputs["presence_manifest"].read_text(encoding="utf-8"))
        runtime_content = project.artifacts.put_bytes(
            runtime.read_bytes(), kind=MigrationArtifact.RUNTIME_ARTIFACT.value
        )
        if (
            runtime_content.digest == previous["runtime_artifact"]["sha256"]
            or presence.get("implementation_bundle_sha256") != implementation["digest"]
            or presence.get("payloads") != payloads
            or presence.get("final_artifact_sha256") != runtime_content.digest
            or presence.get("packaged_test_sha256") != file_sha256(packaged)
        ):
            raise WorkflowError("repair artifact does not bind the new implementation")
        return {
            "implementation_sha256": implementation["digest"],
            "artifact_sha256": runtime_content.digest,
            "artifact_content": asdict(runtime_content),
            "packaged_test_sha256": file_sha256(packaged),
            "packaged_test_path": str(packaged),
            "presence": presence,
            "commands": results,
            "plan": plan,
        }

    def _rerun_plan(
        self,
        project: Project,
        failure: dict[str, Any],
        repair: PublicRepairPlan,
        artifact: dict[str, Any],
    ) -> tuple[PublicQemuPlan, list[str]]:
        previous = PublicQemuPlan.from_dict(failure.get("rerun_plan", failure["plan"]))
        regression = next(
            item for item in previous.ladder if item.level is EvidenceLadderLevel.REGRESSION
        )
        selected = set(repair.failure_run_ids) | set(regression.run_ids)
        old_artifact = next(iter(previous.runs)).artifact_sha256
        replacements = {
            old_artifact: artifact["artifact_sha256"],
            str(project.artifacts.path_for_digest(old_artifact)): str(
                project.artifacts.path_for_digest(artifact["artifact_sha256"])
            ),
        }
        runs = []
        run_ids = {}
        for run in previous.runs:
            if run.run_id not in selected:
                continue
            new_id = f"repair-{artifact['artifact_sha256'][:10]}-{run.run_id}"
            run_ids[run.run_id] = new_id
            runs.append(
                replace(
                    run,
                    run_id=new_id,
                    artifact_sha256=artifact["artifact_sha256"],
                    implementation_sha256=artifact["implementation_sha256"],
                    packaged_test_sha256=artifact["packaged_test_sha256"],
                    qemu=_replace_command(run.qemu, replacements),
                    stimulus=_replace_command(run.stimulus, replacements),
                    checker=_replace_command(run.checker, replacements),
                )
            )
        if {
            run.run_id.removeprefix(f"repair-{artifact['artifact_sha256'][:10]}-") for run in runs
        } != selected:
            raise WorkflowError("repair rerun plan omitted the failing gate or regression")
        ladder = tuple(
            replace(
                item,
                run_ids=tuple(run_ids[run_id] for run_id in item.run_ids if run_id in run_ids),
            )
            for item in previous.ladder
        )
        return PublicQemuPlan(tuple(runs), ladder), sorted(selected)

    def _record_and_finalize(
        self, project: Project, report: dict[str, Any], attempt_dir: Path
    ) -> dict[str, Any]:
        path = attempt_dir / "attempt.json"
        path.write_bytes(self._json(report))
        ref = project.record_artifact(
            MigrationStage.PUBLIC_REPAIR,
            FileArtifact(MigrationArtifact.PUBLIC_REPAIR_ATTEMPT, path),
        )
        if report["status"] == StageStatus.FAIL.value:
            return {"status": StageStatus.RUNNING.value, "attempt": str(path)}
        final = {**report, "attempt_sha256": ref.digest}
        self._finalize(project, final)
        return {
            "status": StageStatus.PASS.value,
            "attempt": str(path),
            "outcome": report["outcome"],
        }

    @staticmethod
    def _finalize(project: Project, report: dict[str, Any]) -> None:
        project.finalize_stage(
            MigrationStage.PUBLIC_REPAIR,
            (
                GeneratedArtifact(
                    MigrationArtifact.PUBLIC_REPAIR_REPORT,
                    PublicRepairService._json(report),
                    "generated:public-repair",
                ),
            ),
        )

    @staticmethod
    def _worktree(project: Project) -> Path:
        acquisition = load_repository_acquisition(project)
        return workspace_path(project, acquisition.target_worktree.path)

    @staticmethod
    def _implementation(project: Project, failure: dict[str, Any]) -> dict[str, Any]:
        prior = failure.get("implementation")
        if isinstance(prior, dict) and isinstance(prior.get("document"), dict):
            return prior
        ref = project.artifact(
            MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE
        )
        return {
            "digest": ref.digest,
            "document": json.loads(project.artifacts.read(ref)),
            "content": ref.to_dict(),
        }

    @staticmethod
    def _artifact_identity(project: Project, failure: dict[str, Any]) -> dict[str, Any]:
        prior = failure.get("artifact_identity")
        if isinstance(prior, dict) and "artifact_sha256" in prior and "plan" in prior:
            return {
                "plan": prior["plan"],
                "runtime_artifact": {"sha256": prior["artifact_sha256"]},
            }
        return project.load_json_artifact(
            MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY
        )

    @staticmethod
    def _latest(project: Project, stage: MigrationStage):
        refs = [
            ref
            for ref in project.current_artifact_refs(stage=stage)
            if ref.kind
            in {
                MigrationArtifact.PUBLIC_QEMU_ATTEMPT.value,
                MigrationArtifact.PUBLIC_REPAIR_ATTEMPT.value,
            }
        ]
        if not refs:
            raise WorkflowError(f"{stage.value} has no preserved attempt")
        ref = refs[-1]
        return ref, json.loads(project.artifacts.read(ref))

    @classmethod
    def _optional_latest(cls, project: Project, stage: MigrationStage):
        refs = [
            ref
            for ref in project.current_artifact_refs(stage=stage)
            if ref.kind == MigrationArtifact.PUBLIC_REPAIR_ATTEMPT.value
        ]
        if not refs:
            return None
        ref = refs[-1]
        return ref, json.loads(project.artifacts.read(ref))

    @staticmethod
    def _evidence(project: Project, references: tuple[dict[str, Any], ...]) -> None:
        knowledge = KnowledgeIndex.for_project(project)
        knowledge.status()
        for reference in references:
            exact = knowledge.show(str(reference.get("chunk_id")))["result"]
            if reference.get("record_id") != exact["record_id"]:
                raise WorkflowError("repair evidence is not a pinned original")
            if file_sha256(knowledge.controlled_path(exact["path"])) != exact["sha256"]:
                raise WorkflowError("repair evidence original changed")

    @staticmethod
    def _json(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise TypeError
    return tuple(value)


def _replace_command(command: PlannedCommand, replacements: dict[str, str]) -> PlannedCommand:
    return PlannedCommand(
        tuple(replacements.get(item, item) for item in command.argv),
        {key: replacements.get(value, value) for key, value in command.environment.items()},
        command.timeout_seconds,
        command.accepted_exit_codes,
    )


def validate_public_repair_bundle(context: BundleValidationContext) -> None:
    report = json_object(
        context.one_current(MigrationArtifact.PUBLIC_REPAIR_REPORT)[1],
        MigrationArtifact.PUBLIC_REPAIR_REPORT.value,
    )
    if report.get("outcome") == ContractExecutionStatus.NOT_APPLICABLE.value:
        if report.get("changed_paths") != []:
            raise WorkflowError("not-applicable public repair changed files")
        return
    attempts = [
        item
        for item in context.current_stage_artifacts
        if item[0].kind == MigrationArtifact.PUBLIC_REPAIR_ATTEMPT.value
        and item[0].digest == report.get("attempt_sha256")
    ]
    if len(attempts) != 1:
        raise WorkflowError("public repair report does not bind one preserved attempt")
    attempt_ref, attempt_data = attempts[0]
    attempt = json_object(attempt_data, MigrationArtifact.PUBLIC_REPAIR_ATTEMPT.value)
    plan = PublicRepairPlan.from_dict(attempt.get("plan"))
    if (
        report.get("attempt_sha256") != attempt_ref.digest
        or attempt.get("status") != StageStatus.PASS.value
        or report.get("plan") != plan.to_dict()
        or (
            report.get("before_implementation_sha256") == report.get("after_implementation_sha256")
            and report.get("outcome") == ContractExecutionStatus.PASS.value
        )
    ):
        raise WorkflowError("public repair final evidence is inconsistent")
    if report.get("outcome") == ContractExecutionStatus.BLOCKED.value:
        if report.get("changed_paths") or report.get("runs"):
            raise WorkflowError("blocked public repair changed code or claimed reruns")
        return
    runs = report.get("runs", [])
    artifact_sha256 = str(report.get("artifact_identity", {}).get("artifact_sha256", ""))
    prefix = f"repair-{artifact_sha256[:10]}-"
    if (
        not runs
        or set(report.get("required_run_ids", []))
        != {
            run["run_id"].removeprefix(prefix)
            for run in runs
            if run.get("run_id", "").startswith(prefix)
        }
        or any(
            run.get("execution_status") != ContractExecutionStatus.PASS.value
            or run.get("attribution") != PublicRunAttribution.TARGET_DRIVER_ON_QEMU.value
            or run.get("actual", {}).get("artifact_sha256")
            != report.get("artifact_identity", {}).get("artifact_sha256")
            for run in runs
        )
    ):
        raise WorkflowError("public repair did not rerun the failing gate and affected regression")
