from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..acquisition.models import CheckoutRecord, RepositoryRole
from ..core.models import ActorRole, StageName, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..core.store import canonical_json
from ..core.validation import validate_artifact
from ..knowledge.index import KnowledgeIndex, file_sha256


class SourceClosureStatus(StrEnum):
    CLOSED = "CLOSED"


class CompilerFamily(StrEnum):
    GCC_COMPATIBLE = "gcc-compatible"


class CoverageStatus(StrEnum):
    COVERED = "COVERED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class ClosureCategory(StrEnum):
    SHARED_CORES = "shared_cores"
    HEADERS = "headers"
    MACROS_CONFIGURATION = "macros_configuration"
    CALLBACKS_FUNCTION_POINTERS = "callbacks_function_pointers"
    REGISTRATION_TABLES = "registration_tables"
    SOURCE_TESTS = "source_tests"
    FRAMEWORK_CONTRACTS = "framework_contracts"


class SourceClosureArtifact(StrEnum):
    CLOSURE = "source_closure"
    VALIDATION_ATTEMPT = "source_closure_validation_attempt"
    VALIDATION_REPORT = "source_closure_report"
    COMPILE_MANIFEST = "compile_manifest"
    COMPILATION_DATABASE = "compilation_database"
    MATERIALS_MANIFEST = "source_closure_materials_manifest"
    KNOWLEDGE_REVISION = "knowledge_revision"


CLOSURE_CATEGORIES = frozenset(category.value for category in ClosureCategory)


@dataclass(frozen=True, slots=True)
class SourceClosureResult:
    status: ValidationStatus
    stage_status: StageStatus
    report_path: str
    errors: tuple[str, ...]


class SourceClosureService:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def validate(self, project: Project, *, closure_path: Path) -> SourceClosureResult:
        project.ensure_role(*self.ROLES)
        stage = project.store.stage(StageName.SOURCE_CLOSURE)
        if stage.status is StageStatus.READY:
            project.start(StageName.SOURCE_CLOSURE)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(
                f"{StageName.SOURCE_CLOSURE} must be READY or RUNNING, got {stage.status.value}"
            )
        controlled_input = self._controlled(project, closure_path)
        input_digest = file_sha256(controlled_input)
        attempt_dir = self._new_attempt_dir(project, input_digest)
        errors: list[str] = []
        details: dict[str, Any] = {}
        compilation_database: list[dict[str, Any]] = []
        compile_manifest: dict[str, Any] = {}
        source_files: dict[str, Path] = {}
        knowledge_revision: dict[str, Any] = {}
        try:
            closure = self._load_json(controlled_input)
            (
                details,
                compilation_database,
                compile_manifest,
                source_files,
            ) = self._validate_closure(project, closure)
            knowledge_revision = self._extend_knowledge(project, source_files)
            if file_sha256(controlled_input) != input_digest:
                raise WorkflowError("source closure submission changed during validation")
        except (WorkflowError, OSError, UnicodeDecodeError, subprocess.SubprocessError) as error:
            errors.append(str(error))
        report = {
            "schema_version": 1,
            "status": ValidationStatus.PASS if not errors else ValidationStatus.FAIL,
            "closure_input": {
                "path": str(controlled_input),
                "sha256": input_digest,
            },
            "details": details,
            "errors": errors,
            "validated_at": utc_now(),
        }
        report_path = attempt_dir / "validation.json"
        report_path.write_bytes(self._json_bytes(report))
        if errors:
            project.add_artifact(
                StageName.SOURCE_CLOSURE,
                SourceClosureArtifact.VALIDATION_ATTEMPT,
                report_path,
            )
            return SourceClosureResult(
                ValidationStatus.FAIL,
                StageStatus.RUNNING,
                str(report_path),
                tuple(errors),
            )

        compilation_path = attempt_dir / "compile_commands.json"
        compilation_bytes = self._json_array_bytes(compilation_database)
        compilation_path.write_bytes(compilation_bytes)
        compile_manifest["compilation_database_sha256"] = hashlib.sha256(
            compilation_bytes
        ).hexdigest()
        compile_manifest_path = attempt_dir / "compile-manifest.json"
        compile_manifest_path.write_bytes(self._json_bytes(compile_manifest))
        knowledge_path = attempt_dir / "knowledge-revision.json"
        knowledge_path.write_bytes(self._json_bytes(knowledge_revision))
        artifacts = (
            (SourceClosureArtifact.CLOSURE, controlled_input),
            (SourceClosureArtifact.VALIDATION_REPORT, report_path),
            (SourceClosureArtifact.COMPILE_MANIFEST, compile_manifest_path),
            (SourceClosureArtifact.COMPILATION_DATABASE, compilation_path),
            (
                SourceClosureArtifact.MATERIALS_MANIFEST,
                project.root / "knowledge" / "manifests" / "materials.jsonl",
            ),
            (SourceClosureArtifact.KNOWLEDGE_REVISION, knowledge_path),
        )
        for kind, path in artifacts:
            validate_artifact(kind, path.read_bytes())
        project.add_artifact(
            StageName.SOURCE_CLOSURE,
            SourceClosureArtifact.CLOSURE,
            controlled_input,
        )
        project.add_artifact(
            StageName.SOURCE_CLOSURE,
            SourceClosureArtifact.VALIDATION_REPORT,
            report_path,
        )
        project.add_artifact(
            StageName.SOURCE_CLOSURE,
            SourceClosureArtifact.COMPILE_MANIFEST,
            compile_manifest_path,
        )
        project.add_artifact(
            StageName.SOURCE_CLOSURE,
            SourceClosureArtifact.COMPILATION_DATABASE,
            compilation_path,
        )
        project.add_artifact(
            StageName.SOURCE_CLOSURE,
            SourceClosureArtifact.MATERIALS_MANIFEST,
            project.root / "knowledge" / "manifests" / "materials.jsonl",
        )
        project.add_artifact(
            StageName.SOURCE_CLOSURE,
            SourceClosureArtifact.KNOWLEDGE_REVISION,
            knowledge_path,
        )
        project.complete(StageName.SOURCE_CLOSURE, StageStatus.PASS)
        return SourceClosureResult(
            ValidationStatus.PASS,
            StageStatus.PASS,
            str(report_path),
            (),
        )

    def _validate_closure(
        self, project: Project, closure: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Path]]:
        required = {
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
        self._require_fields(closure, required, "source closure")
        if (
            closure["schema_version"] != 1
            or closure["closure_status"] != SourceClosureStatus.CLOSED
        ):
            raise WorkflowError("source closure must be schema_version=1 and CLOSED")
        acquisition = project.load_json_artifact("evidence_acquisition", "acquisition_manifest")
        checkouts = tuple(CheckoutRecord.from_dict(record) for record in acquisition["checkouts"])
        source = self._checkout(checkouts, RepositoryRole.SOURCE)
        source_root = (project.root / source.checkout_path).resolve()
        if str(Path(closure["source_root"]).resolve()) != str(source_root):
            raise WorkflowError("source closure root is not the frozen source baseline")
        if closure["source_revision"] != source.resolved_commit:
            raise WorkflowError("source closure revision differs from the frozen source revision")
        unresolved = closure["unresolved_dependencies"]
        if not isinstance(unresolved, list) or unresolved:
            raise WorkflowError("source closure cannot close with unresolved dependencies")
        compiler = closure["compiler"]
        self._require_fields(
            compiler,
            {"family", "executable", "version", "target_abi", "language_mode"},
            "source compiler",
        )
        try:
            compiler_family = CompilerFamily(compiler["family"])
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
        compiler_version = self._compiler_version(Path(executable))
        if str(compiler["version"]) not in compiler_version:
            raise WorkflowError("declared compiler version does not match executable --version")
        defines = closure["defines"]
        if not isinstance(defines, list) or not all(
            isinstance(item, str) and item for item in defines
        ):
            raise WorkflowError("source closure defines must be a string list")
        includes = closure["include_paths"]
        if not isinstance(includes, list) or not all(
            isinstance(item, str) and item for item in includes
        ):
            raise WorkflowError("source closure include_paths must be a list")
        resolved_include_paths = [
            self._source_path(source_root, item, require_file=False) for item in includes
        ]
        include_paths = [str(path) for path in resolved_include_paths]
        conditional = closure["selected_conditional_branches"]
        if (
            not isinstance(conditional, list)
            or not conditional
            or not all(isinstance(item, str) and item for item in conditional)
        ):
            raise WorkflowError("selected conditional-compilation branches must be frozen")
        for collection in ("configuration_inputs", "generated_headers"):
            value = closure[collection]
            self._require_fields(value, {"status", "paths", "rationale"}, collection)
            try:
                collection_status = CoverageStatus(value["status"])
            except (TypeError, ValueError):
                raise WorkflowError(f"{collection} has invalid status")
            if not isinstance(value["paths"], list):
                raise WorkflowError(f"{collection}.paths must be a list")
            if collection_status is CoverageStatus.COVERED and not value["paths"]:
                raise WorkflowError(f"{collection} COVERED requires at least one path")
            if (
                collection_status is CoverageStatus.NOT_APPLICABLE
                and not str(value["rationale"]).strip()
            ):
                raise WorkflowError(f"{collection} NOT_APPLICABLE requires a rationale")

        source_files: dict[str, Path] = {}
        units = closure["translation_units"]
        if not isinstance(units, list) or not units:
            raise WorkflowError("source closure requires at least one translation unit")
        compilation_database: list[dict[str, Any]] = []
        unit_records = []
        unit_ids: set[str] = set()
        unit_source_paths: set[Path] = set()
        observed_defines: set[str] = set()
        observed_include_paths: set[Path] = set()
        for unit in units:
            self._require_fields(
                unit,
                {
                    "unit_id",
                    "source_path",
                    "sha256",
                    "compile_directory",
                    "arguments",
                    "dependencies",
                },
                "translation unit",
            )
            if not isinstance(unit["unit_id"], str) or not unit["unit_id"]:
                raise WorkflowError("translation unit unit_id must be a non-empty string")
            if unit["unit_id"] in unit_ids:
                raise WorkflowError(f"duplicate translation unit id: {unit['unit_id']}")
            unit_ids.add(unit["unit_id"])
            source_path = self._source_path(source_root, unit["source_path"])
            if source_path in unit_source_paths:
                raise WorkflowError(f"duplicate translation unit source: {unit['source_path']}")
            unit_source_paths.add(source_path)
            if file_sha256(source_path) != str(unit["sha256"]).lower():
                raise WorkflowError(f"translation unit hash mismatch: {unit['source_path']}")
            compile_directory = self._source_directory(source_root, unit["compile_directory"])
            arguments = unit["arguments"]
            if (
                not isinstance(arguments, list)
                or not arguments
                or not all(isinstance(argument, str) and argument for argument in arguments)
            ):
                raise WorkflowError("translation unit arguments must be a non-empty argv list")
            argument_compiler = shutil.which(arguments[0])
            if not argument_compiler or Path(argument_compiler).resolve() != compiler_path:
                raise WorkflowError("translation unit argv does not use the frozen compiler")
            if not self._arguments_contain_source(arguments, compile_directory, source_path):
                raise WorkflowError(
                    f"translation unit argv does not compile its source_path: {unit['source_path']}"
                )
            expected_language = f"-std={compiler['language_mode']}"
            if expected_language not in arguments:
                raise WorkflowError(
                    f"translation unit argv does not select {compiler['language_mode']}"
                )
            observed_defines.update(self._option_values(arguments, "-D"))
            observed_include_paths.update(
                self._resolved_include_arguments(arguments, compile_directory)
            )
            dependencies = unit["dependencies"]
            if not isinstance(dependencies, list):
                raise WorkflowError("translation unit dependencies must be a list")
            dependency_records = []
            declared_dependency_paths: set[Path] = set()
            for dependency in dependencies:
                self._require_fields(dependency, {"path", "sha256", "role"}, "source dependency")
                dependency_path = self._source_path(source_root, dependency["path"])
                if file_sha256(dependency_path) != str(dependency["sha256"]).lower():
                    raise WorkflowError(f"source dependency hash mismatch: {dependency['path']}")
                relative = dependency_path.relative_to(source_root).as_posix()
                source_files[relative] = dependency_path
                declared_dependency_paths.add(dependency_path)
                dependency_records.append(
                    {
                        "path": relative,
                        "sha256": file_sha256(dependency_path),
                        "role": dependency["role"],
                    }
                )
            discovered_dependencies = self._compiler_dependencies(
                compiler_family,
                arguments,
                compile_directory,
                source_root,
                source_path,
            )
            missing_dependencies = sorted(
                path.relative_to(source_root).as_posix()
                for path in discovered_dependencies - declared_dependency_paths
            )
            if missing_dependencies:
                raise WorkflowError(
                    f"translation unit {unit['unit_id']} omits compiler-discovered dependencies: "
                    + ", ".join(missing_dependencies)
                )
            relative_source = source_path.relative_to(source_root).as_posix()
            source_files[relative_source] = source_path
            compilation_database.append(
                {
                    "directory": str(compile_directory),
                    "file": str(source_path),
                    "arguments": arguments,
                }
            )
            unit_records.append(
                {
                    "unit_id": unit["unit_id"],
                    "source_path": relative_source,
                    "sha256": file_sha256(source_path),
                    "compile_directory": str(compile_directory),
                    "arguments": arguments,
                    "dependencies": dependency_records,
                }
            )

        if observed_defines != set(defines):
            raise WorkflowError(
                "source closure defines differ from the explicit -D compile arguments"
            )
        if observed_include_paths != set(resolved_include_paths):
            raise WorkflowError(
                "source closure include_paths differ from the explicit -I compile arguments"
            )

        categories = closure["closure_categories"]
        if not isinstance(categories, dict) or set(categories) != CLOSURE_CATEGORIES:
            raise WorkflowError(
                "source closure categories must exactly cover: "
                + ", ".join(sorted(CLOSURE_CATEGORIES))
            )
        category_results = {}
        for name, category in categories.items():
            self._require_fields(category, {"status", "paths", "rationale"}, name)
            try:
                category_status = CoverageStatus(category["status"])
            except (TypeError, ValueError):
                raise WorkflowError(f"source closure category {name} has invalid status")
            if not isinstance(category["paths"], list):
                raise WorkflowError(f"source closure category {name}.paths must be a list")
            if category_status is CoverageStatus.COVERED and not category["paths"]:
                raise WorkflowError(f"covered source closure category {name} needs paths")
            if (
                category_status is CoverageStatus.NOT_APPLICABLE
                and not str(category["rationale"]).strip()
            ):
                raise WorkflowError(f"source closure category {name} needs an N/A rationale")
            resolved_paths = []
            for path in category["paths"]:
                resolved = self._source_path(source_root, path)
                relative = resolved.relative_to(source_root).as_posix()
                source_files[relative] = resolved
                resolved_paths.append(relative)
            category_results[name] = {
                "status": category["status"],
                "paths": resolved_paths,
                "rationale": category["rationale"],
            }

        for collection in ("configuration_inputs", "generated_headers"):
            for path in closure[collection]["paths"]:
                resolved = self._source_path(source_root, path)
                source_files[resolved.relative_to(source_root).as_posix()] = resolved
        compile_manifest = {
            "schema_version": 1,
            "source_revision": source.resolved_commit,
            "source_root": str(source_root),
            "compiler": {
                **compiler,
                "resolved_path": str(compiler_path),
                "sha256": file_sha256(compiler_path),
                "version_output": compiler_version,
                "version_output_sha256": hashlib.sha256(
                    compiler_version.encode("utf-8")
                ).hexdigest(),
            },
            "defines": defines,
            "include_paths": include_paths,
            "configuration_inputs": closure["configuration_inputs"],
            "generated_headers": closure["generated_headers"],
            "selected_conditional_branches": conditional,
            "translation_units": unit_records,
        }
        details = {
            "source_revision": source.resolved_commit,
            "translation_unit_count": len(unit_records),
            "controlled_file_count": len(source_files),
            "closure_categories": category_results,
            "compiler_sha256": compile_manifest["compiler"]["sha256"],
        }
        return details, compilation_database, compile_manifest, source_files

    def _extend_knowledge(self, project: Project, source_files: dict[str, Path]) -> dict[str, Any]:
        knowledge = KnowledgeIndex(project.root)
        records = knowledge.load_manifest()
        existing_paths = {str(record["path"]): record for record in records}
        acquisition = project.load_json_artifact("evidence_acquisition", "acquisition_manifest")
        checkouts = tuple(CheckoutRecord.from_dict(record) for record in acquisition["checkouts"])
        source = self._checkout(checkouts, RepositoryRole.SOURCE)
        additions = []
        for relative, path in sorted(source_files.items()):
            workspace_relative = str(path.relative_to(project.root))
            if workspace_relative in existing_paths:
                if existing_paths[workspace_relative]["sha256"] != file_sha256(path):
                    raise WorkflowError(f"existing knowledge hash differs for {relative}")
                continue
            identifier = "source-closure-" + re.sub(r"[^A-Za-z0-9._-]+", "-", relative).strip("-")
            if any(str(record["id"]) == identifier for record in [*records, *additions]):
                identifier += "-" + hashlib.sha256(relative.encode()).hexdigest()[:8]
            additions.append(
                {
                    "id": identifier,
                    "domain": "source",
                    "path": workspace_relative,
                    "source_url": source.source_url,
                    "revision": source.resolved_commit,
                    "acquired_at": utc_now(),
                    "license": "review-required",
                    "redistribution": "unknown",
                    "sha256": file_sha256(path),
                    "original": True,
                    "index": True,
                    "category": "behaviorally-required-source-closure",
                    "notes": "Added by the validated source dependency closure.",
                }
            )
        if additions:
            knowledge.manifest_path.write_text(
                "".join(canonical_json(record) + "\n" for record in [*records, *additions]),
                encoding="utf-8",
            )
            project.store.append_event(
                "knowledge.source_closure_extended",
                {
                    "added_ids": [record["id"] for record in additions],
                    "added_count": len(additions),
                },
            )
        index_status = knowledge.build()
        return {
            "schema_version": 1,
            "reason": "validated source closure expanded the controlled corpus",
            "added_materials": additions,
            "manifest_sha256": file_sha256(knowledge.manifest_path),
            "index_status": index_status,
        }

    @staticmethod
    def _compiler_version(compiler_path: Path) -> str:
        completed = subprocess.run(
            [str(compiler_path), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (completed.stdout + completed.stderr).strip()
        if completed.returncode != 0 or not output:
            raise WorkflowError("source compiler --version did not return usable identity")
        return output

    @staticmethod
    def _compiler_dependencies(
        compiler_family: CompilerFamily,
        arguments: list[str],
        compile_directory: Path,
        source_root: Path,
        source_path: Path,
    ) -> set[Path]:
        if compiler_family is not CompilerFamily.GCC_COMPATIBLE:
            raise WorkflowError(f"no dependency scanner for compiler family {compiler_family}")
        filtered: list[str] = [arguments[0]]
        position = 1
        options_with_values = {"-o", "-MF", "-MT", "-MQ"}
        ignored_options = {"-c", "-S", "-E", "-fsyntax-only", "-M", "-MM", "-MD", "-MMD"}
        while position < len(arguments):
            argument = arguments[position]
            if argument in options_with_values:
                position += 2
                continue
            if argument in ignored_options:
                position += 1
                continue
            if any(
                argument.startswith(option) and len(argument) > len(option)
                for option in options_with_values
            ):
                position += 1
                continue
            filtered.append(argument)
            position += 1
        completed = subprocess.run(
            [*filtered, "-MM", "-MT", "dpf-source-closure"],
            cwd=compile_directory,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            diagnostic = completed.stderr.strip() or completed.stdout.strip()
            raise WorkflowError(
                "compiler dependency scan failed: " + (diagnostic or "no diagnostic")
            )
        dependency_text = completed.stdout.replace("\\\n", " ")
        _, separator, dependency_values = dependency_text.partition(":")
        if not separator:
            raise WorkflowError("compiler dependency scan produced no makefile dependency rule")
        dependencies: set[Path] = set()
        for token in shlex.split(dependency_values):
            path = Path(token)
            resolved = (path if path.is_absolute() else compile_directory / path).resolve()
            if resolved == source_path:
                continue
            if resolved == source_root or source_root in resolved.parents:
                if not resolved.is_file():
                    raise WorkflowError(
                        f"compiler dependency scan named a missing source file: {resolved}"
                    )
                dependencies.add(resolved)
        return dependencies

    @staticmethod
    def _option_values(arguments: list[str], option: str) -> list[str]:
        values: list[str] = []
        position = 1
        while position < len(arguments):
            argument = arguments[position]
            if argument == option:
                if position + 1 >= len(arguments) or not arguments[position + 1]:
                    raise WorkflowError(f"compile argument {option} has no value")
                values.append(arguments[position + 1])
                position += 2
                continue
            if argument.startswith(option) and len(argument) > len(option):
                values.append(argument[len(option) :])
            position += 1
        return values

    @classmethod
    def _resolved_include_arguments(
        cls, arguments: list[str], compile_directory: Path
    ) -> set[Path]:
        paths: set[Path] = set()
        for value in cls._option_values(arguments, "-I"):
            path = Path(value)
            paths.add((path if path.is_absolute() else compile_directory / path).resolve())
        return paths

    @staticmethod
    def _arguments_contain_source(
        arguments: list[str], compile_directory: Path, source_path: Path
    ) -> bool:
        for argument in arguments[1:]:
            if argument.startswith("-"):
                continue
            path = Path(argument)
            resolved = (path if path.is_absolute() else compile_directory / path).resolve()
            if resolved == source_path:
                return True
        return False

    @staticmethod
    def _source_directory(source_root: Path, value: Any) -> Path:
        if not isinstance(value, str) or not value:
            raise WorkflowError("translation unit compile_directory must be a path string")
        path = Path(value)
        resolved = (path if path.is_absolute() else source_root / path).resolve()
        if resolved != source_root and source_root not in resolved.parents:
            raise WorkflowError("translation unit compile_directory escapes source baseline")
        if not resolved.is_dir():
            raise WorkflowError("translation unit compile_directory does not exist")
        return resolved

    @staticmethod
    def _new_attempt_dir(project: Project, input_digest: str) -> Path:
        root = project.control / "source-closure" / "attempts"
        root.mkdir(parents=True, exist_ok=True)
        prefix = input_digest[:16]
        for sequence in range(1, 10000):
            candidate = root / f"{prefix}-{sequence:04d}"
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            return candidate
        raise WorkflowError("source closure attempt sequence is exhausted")

    @staticmethod
    def _source_path(source_root: Path, relative: str, *, require_file: bool = True) -> Path:
        if not isinstance(relative, str) or not relative:
            raise WorkflowError("source closure path must be a non-empty string")
        path = (source_root / relative).resolve()
        if path != source_root and source_root not in path.parents:
            raise WorkflowError(f"source closure path escapes source root: {relative}")
        if require_file and not path.is_file():
            raise WorkflowError(f"source closure file does not exist: {relative}")
        if not require_file and not path.is_dir():
            raise WorkflowError(f"source include directory does not exist: {relative}")
        return path

    @staticmethod
    def _controlled(project: Project, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != project.root and project.root not in resolved.parents:
            raise WorkflowError("source closure submission must remain inside the project")
        if not resolved.is_file():
            raise WorkflowError(f"source closure submission is not a file: {resolved}")
        return resolved

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise WorkflowError(f"invalid source closure JSON: {path}") from error
        if not isinstance(value, dict):
            raise WorkflowError("source closure must be a JSON object")
        return value

    @staticmethod
    def _require_fields(value: Any, fields: set[str], label: str) -> None:
        if not isinstance(value, dict):
            raise WorkflowError(f"{label} must be an object")
        missing = sorted(fields - value.keys())
        if missing:
            raise WorkflowError(f"{label} missing fields: {', '.join(missing)}")

    @staticmethod
    def _checkout(records: tuple[CheckoutRecord, ...], role: RepositoryRole) -> CheckoutRecord:
        matches = [record for record in records if record.role is role]
        if len(matches) != 1:
            raise WorkflowError(f"expected one {role.value} checkout")
        return matches[0]

    @staticmethod
    def _json_bytes(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )

    @staticmethod
    def _json_array_bytes(value: list[dict[str, Any]]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
