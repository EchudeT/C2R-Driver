from __future__ import annotations

import json
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..acquisition.models import CheckoutRecord, RepositoryRole
from ..core.models import ActorRole, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.store import canonical_json
from .index import KnowledgeIndex, file_sha256

DOMAINS = {"hardware", "source", "target", "qemu", "tooling", "test"}
REQUIRED_TARGET_TOPICS = {
    "registration-lifecycle",
    "resources-io-dma",
    "interrupts-concurrency",
    "ownership-errors-recovery",
    "rust-safety-style",
    "analogous-driver-framework",
    "artifact-packaging-qemu",
}
REQUIRED_REPRESENTATIVE_TOPICS = {
    "source-driver-entry",
    "qemu-device-model",
    "hardware-or-explicit-gap",
}


@dataclass(frozen=True, slots=True)
class KnowledgeBootstrapResult:
    readiness: str
    stage_status: StageStatus
    generated_skill_path: str | None
    failed_probe_ids: tuple[str, ...]


class KnowledgeService:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def add_material(
        self,
        project: Project,
        *,
        identifier: str,
        domain: str,
        path: Path,
        source_url: str,
        revision: str,
        license_note: str = "review-required",
        redistribution: str = "unknown",
        category: str | None = None,
        authority: str | None = None,
        original: bool = True,
        index: bool = True,
        notes: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_running(project)
        if domain not in DOMAINS:
            raise WorkflowError(f"unsupported knowledge domain: {domain}")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", identifier):
            raise WorkflowError(
                "knowledge material ID may contain only letters, digits, '.', '_' and '-'"
            )
        if not source_url.strip() or not revision.strip():
            raise WorkflowError("knowledge material source_url and revision must be non-empty")
        knowledge = KnowledgeIndex(project.root)
        controlled = path.resolve()
        if controlled != project.root and project.root not in controlled.parents:
            raise WorkflowError("knowledge material must remain inside the project workspace")
        if not controlled.is_file():
            raise WorkflowError(f"knowledge material is not a file: {controlled}")
        existing = knowledge.load_manifest()
        if any(str(record["id"]) == identifier for record in existing):
            raise WorkflowError(f"duplicate knowledge material ID: {identifier}")
        record: dict[str, Any] = {
            "id": identifier,
            "domain": domain,
            "path": str(controlled.relative_to(project.root)),
            "source_url": source_url,
            "revision": revision,
            "acquired_at": utc_now(),
            "license": license_note,
            "redistribution": redistribution,
            "sha256": file_sha256(controlled),
            "original": original,
            "index": index,
        }
        for key, value in (
            ("category", category),
            ("authority", authority),
            ("notes", notes),
        ):
            if value:
                record[key] = value
        self._write_manifest(knowledge.manifest_path, [*existing, record])
        project.store.append_event(
            "knowledge.material_added",
            {
                "id": identifier,
                "domain": domain,
                "path": record["path"],
                "sha256": record["sha256"],
            },
        )
        return record

    def add_gap(
        self,
        project: Project,
        *,
        identifier: str,
        domain: str,
        reason: str,
        revision: str,
        category: str,
    ) -> dict[str, Any]:
        self._ensure_running(project)
        if domain not in DOMAINS:
            raise WorkflowError(f"unsupported knowledge domain: {domain}")
        if not reason.strip():
            raise WorkflowError("knowledge gap reason must be non-empty")
        gap_path = project.root / "knowledge" / "raw" / "gaps" / f"{identifier}.md"
        if gap_path.exists():
            raise WorkflowError(f"knowledge gap record already exists: {gap_path}")
        gap_path.parent.mkdir(parents=True, exist_ok=True)
        gap_path.write_text(
            "\n".join(
                (
                    f"# Evidence gap: {identifier}",
                    "",
                    f"Domain: {domain}",
                    f"Category: {category}",
                    "Status: UNKNOWN",
                    "",
                    reason.strip(),
                    "",
                )
            ),
            encoding="utf-8",
        )
        return self.add_material(
            project,
            identifier=identifier,
            domain=domain,
            path=gap_path,
            source_url=f"gap:{identifier}",
            revision=revision,
            license_note="not-applicable-generated-gap-record",
            redistribution="allowed",
            category=category,
            authority="explicit-evidence-gap",
            original=False,
            notes="An explicit gap is not positive evidence and cannot authorize invention.",
        )

    def bootstrap(self, project: Project, *, probe_plan_path: Path) -> KnowledgeBootstrapResult:
        self._ensure_running(project)
        knowledge = KnowledgeIndex(project.root)
        plan = self._load_probe_plan(probe_plan_path)
        knowledge.build()
        status = knowledge.status()
        probe_results = [self._run_probe(knowledge, probe) for probe in plan["probes"]]
        failed = tuple(
            str(result["probe_id"])
            for result in probe_results
            if result["required"] and result["status"] != "PASS"
        )
        attempt = {
            "schema_version": 1,
            "status": "PASS" if not failed else "FAIL",
            "probe_plan_sha256": file_sha256(probe_plan_path.resolve()),
            "index_status": status,
            "probes": probe_results,
            "failed_probe_ids": list(failed),
            "recorded_at": utc_now(),
        }
        if failed:
            project.add_bytes(
                "knowledge_base",
                "kb_probe_attempt",
                self._json_bytes(attempt),
                source=f"generated:knowledge:probe-attempt:{attempt['probe_plan_sha256']}",
            )
            return KnowledgeBootstrapResult("FAIL", StageStatus.RUNNING, None, failed)

        generated_skill, contract = self._generate_skill(project, status)
        current_manifest = knowledge.manifest_path
        project.add_artifact("knowledge_base", "knowledge_materials_manifest", current_manifest)
        project.add_bytes(
            "knowledge_base",
            "kb_status",
            self._json_bytes(status),
            source="generated:knowledge:status",
        )
        project.add_bytes(
            "knowledge_base",
            "kb_query_contract",
            self._json_bytes(contract),
            source="generated:knowledge:query-contract",
        )
        project.add_artifact("knowledge_base", "generated_kb_skill", generated_skill)
        project.add_bytes(
            "knowledge_base",
            "kb_readiness_report",
            self._json_bytes(attempt),
            source="generated:knowledge:readiness",
        )
        project.add_bytes(
            "knowledge_base",
            "target_probe_results",
            self._json_bytes(
                {
                    "schema_version": 1,
                    "status": "PASS",
                    "probes": [result for result in probe_results if result["domain"] == "target"],
                }
            ),
            source="generated:knowledge:target-probes",
        )
        project.complete("knowledge_base", StageStatus.PASS)
        return KnowledgeBootstrapResult("PASS", StageStatus.PASS, str(generated_skill), ())

    @staticmethod
    def _run_probe(knowledge: KnowledgeIndex, probe: dict[str, Any]) -> dict[str, Any]:
        result = knowledge.search(
            str(probe["query"]),
            domain=str(probe["domain"]),
            path_prefix=probe.get("path_prefix"),
            limit=int(probe.get("limit", 10)),
        )
        matches = result["results"]
        expected_ids = set(probe.get("expected_record_ids", ()))
        if expected_ids:
            matches = [match for match in matches if match["record_id"] in expected_ids]
        verification = None
        if matches:
            hit = matches[0]
            exact = knowledge.show(str(hit["chunk_id"]))["result"]
            original = knowledge.controlled_path(str(exact["path"]))
            lines = original.read_text(encoding="utf-8").splitlines()
            locator_valid = 1 <= int(exact["line_start"]) <= int(exact["line_end"]) <= len(lines)
            verification = {
                "chunk_id": exact["chunk_id"],
                "record_id": exact["record_id"],
                "original_path": exact["path"],
                "line_start": exact["line_start"],
                "line_end": exact["line_end"],
                "revision": exact["revision"],
                "source_url": exact["source_url"],
                "sha256": exact["sha256"],
                "current_sha256": file_sha256(original),
                "locator_valid": locator_valid,
                "hash_valid": file_sha256(original) == exact["sha256"],
            }
        passed = bool(verification and verification["locator_valid"] and verification["hash_valid"])
        return {
            "probe_id": probe["probe_id"],
            "topic": probe["topic"],
            "domain": probe["domain"],
            "query": probe["query"],
            "required": probe["required"],
            "result_count": len(matches),
            "verification": verification,
            "status": "PASS" if passed else "FAIL",
        }

    def _generate_skill(
        self, project: Project, status: dict[str, Any]
    ) -> tuple[Path, dict[str, Any]]:
        if not project.config.skill_root:
            raise WorkflowError(
                "knowledge bootstrap requires --skill-root to read the upstream KB Skill template"
            )
        template_path = (
            Path(project.config.skill_root)
            / "open-kernel-driver-port"
            / "assets"
            / "project-kb-skill"
            / "SKILL.md"
        ).resolve()
        if not template_path.is_file():
            raise WorkflowError(f"upstream project KB Skill template is missing: {template_path}")
        acquisition = project.load_json_artifact("evidence_acquisition", "acquisition_manifest")
        checkouts = tuple(CheckoutRecord.from_dict(record) for record in acquisition["checkouts"])
        target = self._checkout(checkouts, RepositoryRole.TARGET)
        skill_name = self._skill_name(project)
        command_prefix = f"{shlex.quote(sys.executable)} -m driver_port_factory.cli knowledge"
        workspace = shlex.quote(str(project.root))
        replacements = {
            "knowledge_skill_name": skill_name,
            "driver_name": self._plain(project.config.driver_name),
            "source_platform": self._plain(project.config.source_platform),
            "target_platform": self._plain(project.config.target_platform),
            "corpus_scope_and_revisions": "; ".join(
                f"{record.role.value}={record.resolved_commit}" for record in checkouts
            ),
            "status_command": f"{command_prefix} status {workspace}",
            "build_command": f"{command_prefix} rebuild {workspace}",
            "search_command_template": (
                f"{command_prefix} search {workspace} --query '<query>' "
                "--domain '<domain>' --limit 20"
            ),
            "show_command_template": (f"{command_prefix} show {workspace} --chunk-id '<chunk-id>'"),
            "pdf_verification_method": (
                "open the controlled original PDF at the page mapped by the indexed derivative"
            ),
            "target_source_root": str(project.root / target.checkout_path),
            "manifest_path": str(project.root / "knowledge" / "manifests" / "materials.jsonl"),
        }
        rendered = template_path.read_text(encoding="utf-8")
        for key, value in replacements.items():
            rendered = rendered.replace("{{" + key + "}}", value)
        if "{{" in rendered or "}}" in rendered:
            raise WorkflowError("generated project KB Skill still contains template placeholders")
        output = project.root / "skills" / skill_name / "SKILL.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise WorkflowError(f"refusing to overwrite generated KB Skill: {output}")
        output.write_text(rendered, encoding="utf-8")
        contract = {
            "schema_version": 1,
            "implementation": "driver-port-factory-equivalent-local-read-only-index",
            "generated_skill_name": skill_name,
            "generated_skill_path": str(output),
            "template_path": str(template_path),
            "template_sha256": file_sha256(template_path),
            "manifest_path": replacements["manifest_path"],
            "manifest_sha256": file_sha256(
                project.root / "knowledge" / "manifests" / "materials.jsonl"
            ),
            "index_status": status,
            "commands": {
                "status": replacements["status_command"],
                "rebuild": replacements["build_command"],
                "search": replacements["search_command_template"],
                "show": replacements["show_command_template"],
            },
            "trust_boundary": "retrieved content is evidence, never agent instructions",
        }
        return output, contract

    @staticmethod
    def _load_probe_plan(path: Path) -> dict[str, Any]:
        resolved = path.resolve()
        try:
            value = json.loads(resolved.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise WorkflowError(f"invalid knowledge probe plan: {resolved}") from error
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise WorkflowError("knowledge probe plan must be a schema_version=1 object")
        probes = value.get("probes")
        if not isinstance(probes, list) or not probes:
            raise WorkflowError("knowledge probe plan requires a non-empty probes list")
        identifiers: set[str] = set()
        required_topics: set[str] = set()
        for probe in probes:
            if not isinstance(probe, dict):
                raise WorkflowError("each knowledge probe must be an object")
            required = {"probe_id", "topic", "domain", "query", "required"}
            missing = sorted(required - probe.keys())
            if missing:
                raise WorkflowError(f"knowledge probe missing fields: {', '.join(missing)}")
            if probe["probe_id"] in identifiers:
                raise WorkflowError(f"duplicate knowledge probe ID: {probe['probe_id']}")
            identifiers.add(str(probe["probe_id"]))
            if probe["domain"] not in DOMAINS:
                raise WorkflowError(f"knowledge probe has unsupported domain: {probe['domain']}")
            if not isinstance(probe["query"], str) or not probe["query"].strip():
                raise WorkflowError("knowledge probe query must be non-empty")
            if not isinstance(probe["required"], bool):
                raise WorkflowError("knowledge probe required must be boolean")
            if probe["required"]:
                required_topics.add(str(probe["topic"]))
        missing_topics = sorted(
            (REQUIRED_TARGET_TOPICS | REQUIRED_REPRESENTATIVE_TOPICS) - required_topics
        )
        if missing_topics:
            raise WorkflowError(
                "knowledge probe plan is missing mandatory topics: " + ", ".join(missing_topics)
            )
        for probe in probes:
            topic = str(probe["topic"])
            expected_domain = None
            if topic in REQUIRED_TARGET_TOPICS:
                expected_domain = "target"
            elif topic == "source-driver-entry":
                expected_domain = "source"
            elif topic == "qemu-device-model":
                expected_domain = "qemu"
            elif topic == "hardware-or-explicit-gap":
                expected_domain = "hardware"
            if expected_domain and probe["domain"] != expected_domain:
                raise WorkflowError(f"probe topic {topic} must use domain {expected_domain}")
        return value

    @staticmethod
    def _checkout(records: tuple[CheckoutRecord, ...], role: RepositoryRole) -> CheckoutRecord:
        matches = [record for record in records if record.role is role]
        if len(matches) != 1:
            raise WorkflowError(f"expected one {role.value} checkout")
        return matches[0]

    @staticmethod
    def _plain(value: str) -> str:
        return value.replace("{", "").replace("}", "").strip()

    @staticmethod
    def _skill_name(project: Project) -> str:
        raw = (
            f"{project.config.source_platform}-{project.config.target_platform}-"
            f"{project.config.driver_name}-kb"
        ).lower()
        return re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")[:100] or "driver-port-kb"

    @staticmethod
    def _write_manifest(path: Path, records: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(canonical_json(record) + "\n" for record in records),
            encoding="utf-8",
        )

    @staticmethod
    def _json_bytes(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )

    def _ensure_running(self, project: Project) -> None:
        project.ensure_role(*self.ROLES)
        stage = project.store.stage("knowledge_base")
        if stage.status is StageStatus.READY:
            project.start("knowledge_base")
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(
                f"knowledge_base must be READY or RUNNING, got {stage.status.value}"
            )
