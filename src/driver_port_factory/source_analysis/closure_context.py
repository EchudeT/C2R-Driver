from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..acquisition.models import CheckoutRecord, RepositoryRole
from ..core.models import WorkflowError
from ..core.project import Project
from .closure_model import ClosureContext
from .closure_paths import checkout, require_fields, source_path
from .compiler import CompilerFamily, compiler_adapter
from .contracts import CoverageStatus, SourceClosureStatus


class ClosureContextValidator:
    REQUIRED_FIELDS = frozenset(
        {
            "schema_version",
            "source_root",
            "source_revision",
            "closure_status",
            "compiler",
            "defines",
            "include_paths",
            "configuration_inputs",
            "generated_headers",
            "selected_conditional_branches",
            "translation_units",
            "closure_categories",
            "unresolved_dependencies",
        }
    )
    COMPILER_FIELDS = frozenset(
        {
            "family",
            "executable",
            "version",
            "target_triple",
            "target_abi",
            "language_mode",
        }
    )

    def validate(self, project: Project, closure: dict[str, Any]) -> ClosureContext:
        require_fields(closure, self.REQUIRED_FIELDS, "source closure")
        self._validate_state(closure)
        source = self._source_checkout(project)
        source_root = (project.root / source.checkout_path).resolve()
        self._validate_source_identity(closure, source, source_root)
        compiler, compiler_path, compiler_version, adapter = self._compiler(closure)
        defines = self._string_list(closure["defines"], "source closure defines")
        includes = self._string_list(closure["include_paths"], "source closure include_paths")
        resolved_includes = tuple(
            source_path(source_root, value, require_file=False) for value in includes
        )
        conditional = self._string_list(
            closure["selected_conditional_branches"],
            "selected conditional-compilation branches",
            require_nonempty=True,
        )
        for collection in ("configuration_inputs", "generated_headers"):
            self._validate_collection(closure[collection], collection)
        return ClosureContext(
            source,
            source_root,
            compiler,
            compiler_path,
            compiler_version,
            adapter,
            tuple(defines),
            resolved_includes,
            tuple(conditional),
        )

    @staticmethod
    def _validate_state(closure: dict[str, Any]) -> None:
        if (
            closure["schema_version"] != 1
            or closure["closure_status"] != SourceClosureStatus.CLOSED
        ):
            raise WorkflowError("source closure must be schema_version=1 and CLOSED")
        unresolved = closure["unresolved_dependencies"]
        if not isinstance(unresolved, list) or unresolved:
            raise WorkflowError("source closure cannot close with unresolved dependencies")

    @staticmethod
    def _source_checkout(project: Project) -> CheckoutRecord:
        acquisition = project.load_json_artifact(
            AcquisitionStage.EVIDENCE_ACQUISITION,
            AcquisitionArtifact.ACQUISITION_MANIFEST,
        )
        records = tuple(CheckoutRecord.from_dict(record) for record in acquisition["checkouts"])
        return checkout(records, RepositoryRole.SOURCE)

    @staticmethod
    def _validate_source_identity(
        closure: dict[str, Any], source: CheckoutRecord, source_root: Path
    ) -> None:
        if str(Path(closure["source_root"]).resolve()) != str(source_root):
            raise WorkflowError("source closure root is not the frozen source baseline")
        if closure["source_revision"] != source.resolved_commit:
            raise WorkflowError("source closure revision differs from the frozen source revision")

    def _compiler(self, closure: dict[str, Any]) -> tuple[dict[str, Any], Path, str, Any]:
        compiler = require_fields(closure["compiler"], self.COMPILER_FIELDS, "source compiler")
        try:
            adapter = compiler_adapter(CompilerFamily(compiler["family"]))
        except (TypeError, ValueError) as error:
            raise WorkflowError(
                f"unsupported source compiler family: {compiler['family']}"
            ) from error
        executable = shutil.which(str(compiler["executable"]))
        if not executable:
            raise WorkflowError(f"source compiler is unavailable: {compiler['executable']}")
        compiler_path = Path(executable).resolve()
        if not all(str(compiler[field]).strip() for field in compiler):
            raise WorkflowError("source compiler identity fields must be non-empty")
        compiler_version = adapter.version(compiler_path)
        if str(compiler["version"]) not in compiler_version:
            raise WorkflowError("declared compiler version does not match executable --version")
        if not isinstance(compiler["target_abi"], dict):
            raise WorkflowError("source compiler target_abi must be a structured ABI object")
        return compiler, compiler_path, compiler_version, adapter

    @staticmethod
    def _string_list(value: Any, label: str, *, require_nonempty: bool = False) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise WorkflowError(f"{label} must be a string list")
        if require_nonempty and not value:
            raise WorkflowError(f"{label} must be frozen")
        return value

    @staticmethod
    def _validate_collection(value: Any, label: str) -> None:
        record = require_fields(value, {"status", "paths", "rationale"}, label)
        try:
            status = CoverageStatus(record["status"])
        except (TypeError, ValueError) as error:
            raise WorkflowError(f"{label} has invalid status") from error
        if not isinstance(record["paths"], list):
            raise WorkflowError(f"{label}.paths must be a list")
        if status is CoverageStatus.COVERED and not record["paths"]:
            raise WorkflowError(f"{label} COVERED requires at least one path")
        if status is CoverageStatus.NOT_APPLICABLE and not str(record["rationale"]).strip():
            raise WorkflowError(f"{label} NOT_APPLICABLE requires a rationale")
