from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.models import ActorRole, IntakeStatus, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from .catalog import DriverCandidate, DriverCatalog, Resolution


@dataclass(frozen=True, slots=True)
class IntakeAnalysisResult:
    status: IntakeStatus
    candidates: tuple[DriverCandidate, ...]
    question: str | None = None
    selected_candidate_id: str | None = None


class IntakeService:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def analyze(
        self,
        project: Project,
        *,
        raw_request: str,
        catalog_path: Path | None = None,
    ) -> IntakeAnalysisResult:
        project.ensure_role(*self.ROLES)
        if not raw_request.strip():
            raise WorkflowError("raw migration request must not be empty")
        if project.store.stage("request_intake").status is not StageStatus.READY:
            raise WorkflowError(
                "intake was already analyzed; use `dpf intake show` or answer the stored question"
            )
        catalog = (
            DriverCatalog.from_file(catalog_path)
            if catalog_path
            else DriverCatalog.builtin(project.config.source_platform)
        )
        if catalog.source_platform.casefold() != project.config.source_platform.casefold():
            raise WorkflowError(
                f"catalog platform {catalog.source_platform} does not match "
                f"{project.config.source_platform}"
            )
        project.start("request_intake")
        request = {
            "schema_version": 1,
            "source_platform": project.config.source_platform,
            "target_platform": project.config.target_platform,
            "user_supplied_driver_name": project.config.driver_name,
            "raw_request": raw_request,
            "recorded_at": utc_now(),
            "intake_status": IntakeStatus.ANALYZING.value,
        }
        self._add_json(project, "request_intake", "request_record", request)
        project.complete("request_intake", StageStatus.PASS)

        project.start("driver_candidate_resolution")
        resolution = catalog.resolve(project.config.driver_name)
        candidate_record = {
            "schema_version": 1,
            "query": resolution.query,
            "match_type": resolution.match_type,
            "auto_confirmable": resolution.auto_confirmable,
            "metadata_scope": "LIGHTWEIGHT_ONLY",
            "catalog": {
                "id": catalog.catalog_id,
                "version": catalog.catalog_version,
                "sha256": catalog.digest,
                "source": catalog.source,
            },
            "candidates": [candidate.to_dict() for candidate in resolution.candidates],
        }
        self._add_json(
            project,
            "driver_candidate_resolution",
            "driver_candidates",
            candidate_record,
        )
        project.complete("driver_candidate_resolution", StageStatus.PASS)

        project.start("scope_confirmation")
        if resolution.auto_confirmable:
            selected = resolution.candidates[0]
            confirmation = self._confirmation(
                selected,
                answer="Automatically confirmed from one exact catalog match.",
                confirmation_basis=[
                    f"exact canonical/alias/source match: {project.config.driver_name}",
                    f"catalog:{catalog.catalog_id}@{catalog.catalog_version}:{catalog.digest}",
                ],
                automatic=True,
            )
            self._add_json(
                project,
                "scope_confirmation",
                "scope_confirmation",
                confirmation,
            )
            project.complete("scope_confirmation", StageStatus.PASS)
            self._freeze(project, selected, confirmation, resolution.candidates)
            return IntakeAnalysisResult(
                IntakeStatus.FROZEN,
                resolution.candidates,
                selected_candidate_id=selected.candidate_id,
            )

        question = self._question(resolution)
        self._add_json(
            project,
            "scope_confirmation",
            "confirmation_question",
            {
                "schema_version": 1,
                "question": question,
                "candidate_ids": [candidate.candidate_id for candidate in resolution.candidates],
                "question_count": 1,
                "intake_status": IntakeStatus.WAITING_FOR_USER.value,
            },
        )
        project.store.wait_for_user("scope_confirmation", question=question)
        return IntakeAnalysisResult(
            IntakeStatus.WAITING_FOR_USER,
            resolution.candidates,
            question=question,
        )

    def answer(
        self,
        project: Project,
        *,
        candidate_id: str | None,
        answer_text: str,
        canonical_name: str | None = None,
        source_path: str | None = None,
        device_family: str | None = None,
        bus: str | None = None,
        intended_subset: tuple[str, ...] = (),
        excluded_variants: tuple[str, ...] = (),
    ) -> IntakeAnalysisResult:
        project.ensure_role(*self.ROLES)
        if project.store.stage("scope_confirmation").status is not StageStatus.WAITING_FOR_USER:
            raise WorkflowError("scope_confirmation is not waiting for a user answer")
        candidate_record = self._load_json(
            project, "driver_candidate_resolution", "driver_candidates"
        )
        candidates = tuple(
            DriverCandidate.from_dict(item) for item in candidate_record["candidates"]
        )
        selected = next(
            (candidate for candidate in candidates if candidate.candidate_id == candidate_id),
            None,
        )
        if selected is None:
            required = {
                "canonical_name": canonical_name,
                "source_path": source_path,
                "device_family": device_family,
                "bus": bus,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                known = ", ".join(candidate.candidate_id for candidate in candidates) or "none"
                raise WorkflowError(
                    f"unknown candidate {candidate_id!r}; known candidates: {known}. "
                    f"A manual candidate requires: {', '.join(missing)}"
                )
            selected = DriverCandidate(
                candidate_id=candidate_id or "manual-user-confirmed",
                canonical_name=str(canonical_name),
                source_entry_hint=str(source_path),
                device_family=str(device_family),
                bus_or_transport=str(bus),
                aliases=(project.config.driver_name,),
                device_scope=intended_subset or (str(device_family),),
            )
        project.store.resume_after_user(
            "scope_confirmation",
            project.config.actor_role,
            answer=answer_text,
        )
        confirmation = self._confirmation(
            selected,
            answer=answer_text,
            confirmation_basis=["explicit user selection from persisted candidate question"],
            automatic=False,
            intended_subset=intended_subset,
            excluded_variants=excluded_variants,
        )
        self._add_json(
            project,
            "scope_confirmation",
            "scope_confirmation",
            confirmation,
        )
        project.complete("scope_confirmation", StageStatus.PASS)
        self._freeze(project, selected, confirmation, candidates)
        return IntakeAnalysisResult(
            IntakeStatus.FROZEN,
            candidates,
            selected_candidate_id=selected.candidate_id,
        )

    def show(self, project: Project) -> dict[str, Any]:
        names = (
            "request_intake",
            "driver_candidate_resolution",
            "scope_confirmation",
            "migration_envelope_freeze",
        )
        result: dict[str, Any] = {
            "project_id": project.config.project_id,
            "stages": {name: project.store.stage(name).status.value for name in names},
        }
        for stage, kind, key in (
            ("driver_candidate_resolution", "driver_candidates", "resolution"),
            ("scope_confirmation", "confirmation_question", "question"),
            ("scope_confirmation", "scope_confirmation", "confirmation"),
            ("migration_envelope_freeze", "migration_envelope", "migration_envelope"),
        ):
            try:
                result[key] = self._load_json(project, stage, kind)
            except WorkflowError:
                continue
        return result

    def _freeze(
        self,
        project: Project,
        selected: DriverCandidate,
        confirmation: dict[str, Any],
        candidates: tuple[DriverCandidate, ...],
    ) -> None:
        project.start("migration_envelope_freeze")
        excluded = list(confirmation["excluded_variants"])
        if not excluded:
            excluded = [
                f"{candidate.canonical_name}:{candidate.bus_or_transport}"
                for candidate in candidates
                if candidate.candidate_id != selected.candidate_id
            ]
        envelope = {
            "schema_version": 1,
            "source_platform": project.config.source_platform,
            "target_platform": project.config.target_platform,
            "user_supplied_driver_name": project.config.driver_name,
            "canonical_source_driver_name": selected.canonical_name,
            "source_driver_entry_or_repository_hint": selected.source_entry_hint,
            "device_family": selected.device_family,
            "bus_or_transport": selected.bus_or_transport,
            "candidate_device_ids": list(selected.candidate_device_ids),
            "intended_subset": list(confirmation["intended_subset"]),
            "excluded_variants": excluded,
            "qemu_models": list(selected.qemu_models),
            "identity_status": IntakeStatus.FROZEN.value,
            "confirmation_basis": list(confirmation["confirmation_basis"]),
            "selected_candidate_id": selected.candidate_id,
        }
        identity_record = {
            "canonical_name": selected.canonical_name,
            "bus": selected.bus_or_transport,
            "device_scope": list(confirmation["intended_subset"]),
            "source_paths": [selected.source_entry_hint],
            "confirmed": True,
            "candidate_device_ids": list(selected.candidate_device_ids),
            "excluded_variants": excluded,
            "confirmation_basis": list(confirmation["confirmation_basis"]),
        }
        self._add_json(
            project,
            "migration_envelope_freeze",
            "migration_envelope",
            envelope,
        )
        self._add_json(
            project,
            "migration_envelope_freeze",
            "identity_record",
            identity_record,
        )
        project.complete("migration_envelope_freeze", StageStatus.PASS)

    @staticmethod
    def _confirmation(
        selected: DriverCandidate,
        *,
        answer: str,
        confirmation_basis: list[str],
        automatic: bool,
        intended_subset: tuple[str, ...] = (),
        excluded_variants: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "selected_candidate": selected.to_dict(),
            "answer": answer,
            "automatic": automatic,
            "intended_subset": list(intended_subset or selected.device_scope),
            "excluded_variants": list(excluded_variants),
            "confirmation_basis": confirmation_basis,
            "identity_status": IntakeStatus.CONFIRMED.value,
        }

    @staticmethod
    def _question(resolution: Resolution) -> str:
        if not resolution.candidates:
            return (
                f"无法把“{resolution.query}”唯一对应到一个已知源驱动。"
                "请提供规范驱动名或源码路径，并注明设备系列及总线/传输类型。"
            )
        options = "；".join(
            f"{candidate.candidate_id}={candidate.canonical_name} "
            f"({candidate.bus_or_transport}, {candidate.source_entry_hint})"
            for candidate in resolution.candidates
        )
        return f"“{resolution.query}”对应多个驱动范围：{options}。请选择一个 candidate_id。"

    @staticmethod
    def _add_json(project: Project, stage: str, kind: str, value: dict[str, Any]) -> None:
        project.add_bytes(
            stage,
            kind,
            (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
                "utf-8"
            ),
            source=f"generated:intake:{kind}",
        )

    @staticmethod
    def _load_json(project: Project, stage: str, kind: str) -> dict[str, Any]:
        refs = [
            ref
            for ref in project.store.artifact_refs(stage=stage, direction="output")
            if ref.kind == kind
        ]
        if len(refs) != 1:
            raise WorkflowError(f"expected one {kind} artifact in {stage}, found {len(refs)}")
        value = json.loads(project.artifacts.read(refs[0]))
        if not isinstance(value, dict):
            raise WorkflowError(f"{kind} is not a JSON object")
        return value
