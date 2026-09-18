from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path
from typing import Any

from ..acquisition.repository import load_repository_acquisition
from ..acquisition.repository_checkout import CheckoutRecord
from ..acquisition.repository_role import RepositoryRole
from ..core.models import WorkflowError
from ..core.project import Project
from .corpus import CorpusManifest
from .index import file_sha256


class ProjectKnowledgeSkillGenerator:
    def generate(
        self,
        project: Project,
        status: dict[str, Any],
        manifest: CorpusManifest,
    ) -> tuple[Path, dict[str, Any]]:
        template_path = self._template_path(project)
        checkouts = self._checkouts(project)
        target = self._checkout(checkouts, RepositoryRole.TARGET)
        skill_name = self._skill_name(project)
        replacements = self._replacements(project, checkouts, target, skill_name, status, manifest)
        rendered = template_path.read_text(encoding="utf-8")
        for key, value in replacements.items():
            rendered = rendered.replace("{{" + key + "}}", value)
        if "{{" in rendered or "}}" in rendered:
            raise WorkflowError("generated project KB Skill still contains template placeholders")
        output = project.root / "skills" / skill_name / "SKILL.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        contract = {
            "schema_version": 1,
            "implementation": "driver-port-factory-equivalent-local-read-only-index",
            "generated_skill_name": skill_name,
            "generated_skill_path": str(output),
            "template_path": str(template_path),
            "template_sha256": file_sha256(template_path),
            "manifest_path": replacements["manifest_path"],
            "manifest_sha256": manifest.digest,
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
    def _template_path(project: Project) -> Path:
        if not project.config.skill_root:
            raise WorkflowError(
                "knowledge bootstrap requires --skill-root to read the upstream KB Skill template"
            )
        template = (
            Path(project.config.skill_root)
            / "open-kernel-driver-port"
            / "assets"
            / "project-kb-skill"
            / "SKILL.md"
        ).resolve()
        if not template.is_file():
            raise WorkflowError(f"upstream project KB Skill template is missing: {template}")
        return template

    @staticmethod
    def _checkouts(project: Project) -> tuple[CheckoutRecord, ...]:
        return load_repository_acquisition(project).checkouts

    @staticmethod
    def _checkout(records: tuple[CheckoutRecord, ...], role: RepositoryRole) -> CheckoutRecord:
        matches = [record for record in records if record.role is role]
        if len(matches) != 1:
            raise WorkflowError(f"expected one {role.value} checkout")
        return matches[0]

    def _replacements(
        self,
        project: Project,
        checkouts: tuple[CheckoutRecord, ...],
        target: CheckoutRecord,
        skill_name: str,
        status: dict[str, Any],
        manifest: CorpusManifest,
    ) -> dict[str, str]:
        command_prefix = f"{shlex.quote(sys.executable)} -m driver_port_factory.cli knowledge"
        workspace = shlex.quote(str(project.root))
        return {
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
                "--domain '<domain>' --limit 5"
            ),
            "show_command_template": (f"{command_prefix} show {workspace} --chunk-id '<chunk-id>'"),
            "pdf_verification_method": (
                "open the controlled original PDF at the page mapped by the indexed derivative"
            ),
            "target_source_root": str(project.root / target.checkout_path),
            "manifest_path": str(project.root / str(status["manifest_path"])),
        }

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
