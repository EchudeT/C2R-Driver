from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from ..acquisition.repository import load_repository_acquisition
from ..acquisition.repository_role import RepositoryRole
from ..core.models import GeneratedArtifact, StageStatus, WorkflowError
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..knowledge.contracts import KnowledgeArtifact
from ..knowledge.index import file_sha256
from ..source_analysis.contracts import SourceAnalysisArtifact, SourceAnalysisStage
from ..target_study.contracts import ApiConfidence, ChangeLevel, TargetStudyArtifact
from .contracts import (
    ImplementationFileRole,
    MigrationArtifact,
    MigrationStage,
    TargetChangeStatus,
    TestDisposition,
    TranslationDomain,
    TranslationStatus,
)

IMPLEMENTATION_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.CONTRACTS,
    MigrationArtifact.TEST_PORT_MATRIX,
    KnowledgeArtifact.QUERY_CONTRACT,
    KnowledgeArtifact.GENERATED_SKILL,
    TargetStudyArtifact.STRUCTURED_PROFILE,
    TargetStudyArtifact.API_EVIDENCE,
    TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE,
    TargetStudyArtifact.CHANGE_PLAN,
    SourceAnalysisArtifact.SOURCE_CLOSURE,
    SourceAnalysisArtifact.STRUCTURED_C_FACTS,
)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments], text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise WorkflowError(f"Git inspection failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be an object")
    return value


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkflowError(f"{label} must be a string list")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class ImplementationFileDeclaration:
    path: str
    role: ImplementationFileRole

    @classmethod
    def from_dict(cls, value: Any) -> ImplementationFileDeclaration:
        item = _object(value, "implementation file declaration")
        try:
            return cls(str(item["path"]), ImplementationFileRole(item["role"]))
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("implementation file has an invalid typed boundary") from error

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "role": self.role.value}


@dataclass(frozen=True, slots=True)
class ImplementationFile:
    path: str
    role: ImplementationFileRole
    sha256: str

    @classmethod
    def from_dict(cls, value: Any) -> ImplementationFile:
        item = _object(value, "implementation file")
        try:
            result = cls(
                str(item["path"]), ImplementationFileRole(item["role"]), str(item["sha256"])
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("implementation file has an invalid typed boundary") from error
        if len(result.sha256) != 64:
            raise WorkflowError("implementation file requires a SHA256 digest")
        return result

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "role": self.role.value, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class SourceFactReference:
    unit_id: str
    fact_ids: tuple[str, ...]
    source_spans: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, value: Any) -> SourceFactReference:
        item = _object(value, "coverage source reference")
        spans = item.get("source_spans", [])
        if not isinstance(spans, list) or not all(isinstance(span, dict) for span in spans):
            raise WorkflowError("coverage source spans must be objects")
        reference = cls(
            str(item.get("unit_id", "")),
            _strings(item.get("fact_ids"), "fact_ids"),
            tuple(spans),
        )
        if (
            not reference.unit_id
            or not reference.fact_ids
            or len(reference.fact_ids) != len(set(reference.fact_ids))
        ):
            raise WorkflowError("coverage source reference is incomplete")
        return reference

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "fact_ids": list(self.fact_ids),
            "source_spans": list(self.source_spans),
        }


@dataclass(frozen=True, slots=True)
class CoverageRecord:
    identifier: str
    domain: TranslationDomain
    unit_ids: tuple[str, ...]
    source_references: tuple[SourceFactReference, ...]
    target: dict[str, Any]
    contract_ids: tuple[str, ...]
    test_ids: tuple[str, ...]
    lowering: str
    assumptions: tuple[str, ...]
    safety_obligation_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]
    status: TranslationStatus

    @classmethod
    def from_dict(cls, value: Any) -> CoverageRecord:
        item = _object(value, "translation coverage")
        try:
            result = cls(
                str(item["coverage_id"]),
                TranslationDomain(item["domain"]),
                _strings(item["unit_ids"], "unit_ids"),
                tuple(
                    SourceFactReference.from_dict(reference)
                    for reference in item["source_references"]
                ),
                _object(item["target"], "coverage target"),
                _strings(item["contract_ids"], "contract_ids"),
                _strings(item["test_ids"], "test_ids"),
                str(item["lowering"]),
                _strings(item["assumptions"], "assumptions"),
                _strings(item["safety_obligation_ids"], "safety_obligation_ids"),
                _strings(item["diagnostics"], "diagnostics"),
                TranslationStatus(item["status"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("translation coverage has an invalid typed boundary") from error
        if (
            len(result.unit_ids) != len(set(result.unit_ids))
            or (result.domain is TranslationDomain.TEST_ASSERTION and result.unit_ids)
            or (result.domain is not TranslationDomain.TEST_ASSERTION and not result.unit_ids)
        ):
            raise WorkflowError("translation coverage has an invalid unit/domain scope")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "coverage_id": self.identifier,
            "domain": self.domain.value,
            "unit_ids": list(self.unit_ids),
            "source_references": [item.to_dict() for item in self.source_references],
            "target": self.target,
            "contract_ids": list(self.contract_ids),
            "test_ids": list(self.test_ids),
            "lowering": self.lowering,
            "assumptions": list(self.assumptions),
            "safety_obligation_ids": list(self.safety_obligation_ids),
            "diagnostics": list(self.diagnostics),
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class ImplementationResponse:
    file_roles: tuple[ImplementationFileDeclaration, ...]
    coverage: tuple[CoverageRecord, ...] | None
    target_changes: tuple[dict[str, Any], ...] | None
    target_symbols: tuple[dict[str, Any], ...] | None
    unsafe_obligations: tuple[dict[str, Any], ...] | None

    @classmethod
    def read(cls, path: Path) -> ImplementationResponse:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("Codex implementation response is not UTF-8 JSON") from error
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Any) -> ImplementationResponse:
        if not isinstance(value, dict) or value.get("schema_version") != 2:
            raise WorkflowError("implementation response must be a schema_version=2 object")
        try:
            file_roles = value["file_roles"]
            if not isinstance(file_roles, list):
                raise TypeError

            def optional_items(name: str) -> list[Any] | None:
                items = value[name]
                if items is not None and not isinstance(items, list):
                    raise TypeError
                return items

            coverage = optional_items("coverage")
            target_changes = optional_items("target_changes")
            target_symbols = optional_items("target_symbols")
            unsafe_obligations = optional_items("unsafe_obligations")
            return cls(
                tuple(ImplementationFileDeclaration.from_dict(item) for item in file_roles),
                None
                if coverage is None
                else tuple(CoverageRecord.from_dict(item) for item in coverage),
                None
                if target_changes is None
                else tuple(_object(item, "target change") for item in target_changes),
                None
                if target_symbols is None
                else tuple(_object(item, "target symbol") for item in target_symbols),
                None
                if unsafe_obligations is None
                else tuple(_object(item, "unsafe obligation") for item in unsafe_obligations),
            )
        except (KeyError, TypeError) as error:
            raise WorkflowError("implementation response has an invalid typed boundary") from error


class DriverImplementationService:
    def finalize(self, project: Project, response: ImplementationResponse) -> None:
        if project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status is not StageStatus.RUNNING:
            raise WorkflowError("driver_implementation must be RUNNING")
        acquisition = load_repository_acquisition(project)
        worktree = (project.root / acquisition.target_worktree.path).resolve()
        baseline = self.baseline_context(project)
        declared_files = self._resolved_files(worktree, response.file_roles, baseline)
        coverage = self._resolve_source_references(
            project,
            self._resolved_collection(response.coverage, baseline, "coverage"),
        )
        target_changes = self._resolved_collection(
            response.target_changes, baseline, "target_changes"
        )
        target_symbols = self._resolved_collection(
            response.target_symbols, baseline, "target_symbols"
        )
        unsafe_obligations = self._resolved_collection(
            response.unsafe_obligations, baseline, "unsafe_obligations"
        )
        files = []
        for declared in declared_files:
            relative = PurePosixPath(declared.path)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative.as_posix() != declared.path
            ):
                raise WorkflowError(f"implementation path is not canonical: {declared.path}")
            path = (worktree / declared.path).resolve()
            if worktree not in path.parents or not path.is_file() or path.is_symlink():
                raise WorkflowError(
                    f"implementation file is outside the target worktree: {declared.path}"
                )
            data = path.read_bytes()
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise WorkflowError(f"implementation file is not UTF-8: {declared.path}") from error
            files.append(declared.to_dict())
        inputs = self._inputs(project)
        repositories = {
            "baselines": [
                {
                    "role": checkout.role.value,
                    "path": checkout.checkout_path,
                    "commit": checkout.resolved_commit,
                }
                for checkout in acquisition.checkouts
                if checkout.role in {RepositoryRole.SOURCE, RepositoryRole.TARGET}
            ],
            "target_worktree": {
                "path": acquisition.target_worktree.path,
                "base_commit": acquisition.target_worktree.base_commit,
                "branch": acquisition.target_worktree.branch,
            },
        }
        documents = (
            (
                MigrationArtifact.IMPLEMENTATION_BUNDLE,
                {
                    "schema_version": 1,
                    "inputs": inputs,
                    "repositories": repositories,
                    "files": files,
                },
            ),
            (
                MigrationArtifact.TRANSLATION_COVERAGE,
                {
                    "schema_version": 1,
                    "inputs": inputs,
                    "coverage": [record.to_dict() for record in coverage],
                },
            ),
            (
                MigrationArtifact.TARGET_CHANGE_INVENTORY,
                {
                    "schema_version": 1,
                    "inputs": inputs,
                    "target_changes": list(target_changes),
                    "target_symbols": list(target_symbols),
                    "unsafe_obligations": list(unsafe_obligations),
                },
            ),
        )
        project.finalize_stage(
            MigrationStage.DRIVER_IMPLEMENTATION,
            tuple(
                GeneratedArtifact(kind, self._json(value), f"generated:{kind.value}")
                for kind, value in documents
            ),
        )

    @staticmethod
    def baseline_context(project: Project) -> dict[str, Any] | None:
        documents = {}
        for kind in (
            MigrationArtifact.IMPLEMENTATION_BUNDLE,
            MigrationArtifact.TRANSLATION_COVERAGE,
            MigrationArtifact.TARGET_CHANGE_INVENTORY,
        ):
            refs = [
                ref
                for ref in project.artifact_refs(stage=MigrationStage.DRIVER_IMPLEMENTATION)
                if ref.kind == kind.value and ref.ordinal is not None
            ]
            if not refs:
                return None
            ref = max(refs, key=lambda item: item.ordinal)
            documents[kind] = json.loads(project.artifacts.read(ref))
        bundle = documents[MigrationArtifact.IMPLEMENTATION_BUNDLE]
        coverage = documents[MigrationArtifact.TRANSLATION_COVERAGE]
        inventory = documents[MigrationArtifact.TARGET_CHANGE_INVENTORY]
        return {
            "file_roles": [
                ImplementationFileDeclaration.from_dict(item).to_dict() for item in bundle["files"]
            ],
            "coverage": [CoverageRecord.from_dict(item).to_dict() for item in coverage["coverage"]],
            "target_changes": inventory["target_changes"],
            "target_symbols": inventory["target_symbols"],
            "unsafe_obligations": inventory["unsafe_obligations"],
        }

    @staticmethod
    def _resolved_collection(
        proposed: tuple[Any, ...] | None,
        baseline: dict[str, Any] | None,
        name: str,
    ) -> tuple[Any, ...]:
        if proposed is not None:
            return proposed
        if baseline is None:
            raise WorkflowError(f"initial implementation must provide {name}")
        if name == "coverage":
            return tuple(CoverageRecord.from_dict(item) for item in baseline[name])
        return tuple(_object(item, name) for item in baseline[name])

    @staticmethod
    def _resolved_files(
        worktree: Path,
        declarations: tuple[ImplementationFileDeclaration, ...],
        baseline: dict[str, Any] | None,
    ) -> tuple[ImplementationFile, ...]:
        roles = {
            item.path: item.role
            for item in (
                ImplementationFileDeclaration.from_dict(value)
                for value in (baseline or {}).get("file_roles", [])
            )
        }
        for declaration in declarations:
            roles[declaration.path] = declaration.role
        changed = set(
            filter(
                None,
                _git(worktree, "diff", "--name-only", "HEAD").splitlines(),
            )
        )
        changed.update(
            filter(
                None,
                _git(worktree, "ls-files", "--others", "--exclude-standard").splitlines(),
            )
        )
        missing = changed - roles.keys()
        if missing:
            raise WorkflowError(
                "implementation needs roles for new changed paths: " + ", ".join(sorted(missing))
            )
        return tuple(
            ImplementationFile(path, roles[path], file_sha256(worktree / path))
            for path in sorted(changed)
        )

    @staticmethod
    def _resolve_source_references(
        project: Project, coverage: tuple[CoverageRecord, ...]
    ) -> tuple[CoverageRecord, ...]:
        facts = project.load_json_artifact(
            SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
            SourceAnalysisArtifact.STRUCTURED_C_FACTS,
        )
        semantic_refs = project.current_artifact_refs(
            stage=SourceAnalysisStage.STRUCTURED_C_ANALYSIS
        )
        nodes_by_unit: dict[str, dict[str, dict[str, Any]]] = {}
        for unit in facts.get("units", []):
            link = _object(unit.get("semantic_index"), "semantic index reference")
            source = str((project.root / str(link.get("path", ""))).resolve())
            matches = [
                ref
                for ref in semantic_refs
                if ref.kind == SourceAnalysisArtifact.STRUCTURED_C_SEMANTIC_INDEX.value
                and ref.digest == link.get("sha256")
                and ref.source == source
            ]
            if len(matches) != 1:
                raise WorkflowError("coverage cannot resolve a structured semantic index")
            index = json.loads(project.artifacts.read(matches[0]))
            nodes_by_unit[str(unit.get("unit_id"))] = {
                str(node.get("id")): node
                for node in index.get("nodes", [])
                if isinstance(node, dict)
            }

        resolved = []
        for record in coverage:
            references = []
            if record.domain is TranslationDomain.TEST_ASSERTION:
                if record.source_references:
                    raise WorkflowError("test coverage cannot reference C source facts")
            elif {reference.unit_id for reference in record.source_references} != set(
                record.unit_ids
            ):
                raise WorkflowError("coverage source references differ from its source units")
            for reference in record.source_references:
                nodes = nodes_by_unit.get(reference.unit_id, {})
                spans = []
                for fact_id in reference.fact_ids:
                    node = nodes.get(fact_id)
                    if node is None or not ({"loc", "range"} & node.keys()):
                        raise WorkflowError(
                            "coverage references an unknown or unspanned source fact"
                        )
                    span = {"fact_id": fact_id}
                    span.update({field: node[field] for field in ("loc", "range") if field in node})
                    spans.append(span)
                references.append(replace(reference, source_spans=tuple(spans)))
            resolved.append(replace(record, source_references=tuple(references)))
        return tuple(resolved)

    @staticmethod
    def _inputs(project: Project) -> dict[str, Any]:
        dependencies = project.workflow.spec(MigrationStage.DRIVER_IMPLEMENTATION).dependencies
        result = {}
        for kind in IMPLEMENTATION_INPUTS:
            matches = [
                ref
                for stage in dependencies
                for ref in project.current_artifact_refs(stage=stage)
                if ref.kind == kind.value
            ]
            if len(matches) != 1:
                raise WorkflowError(f"driver implementation requires one {kind.value} input")
            result[kind.value] = matches[0].to_dict()
        return result

    @staticmethod
    def _json(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


class DriverImplementationGate:
    def __init__(self, context: BundleValidationContext) -> None:
        self.context = context
        self.bundle = json_object(
            context.one_current(MigrationArtifact.IMPLEMENTATION_BUNDLE)[1], "implementation bundle"
        )
        self.coverage_document = json_object(
            context.one_current(MigrationArtifact.TRANSLATION_COVERAGE)[1], "translation coverage"
        )
        self.inventory = json_object(
            context.one_current(MigrationArtifact.TARGET_CHANGE_INVENTORY)[1],
            "target change inventory",
        )
        self.files = tuple(
            ImplementationFile.from_dict(item) for item in self.bundle.get("files", [])
        )
        self.coverage = tuple(
            CoverageRecord.from_dict(item) for item in self.coverage_document.get("coverage", [])
        )

    def validate(self) -> None:
        expected_inputs = {
            kind.value: self.context.one_dependency(kind)[0].to_dict()
            for kind in IMPLEMENTATION_INPUTS
        }
        if any(
            document.get("inputs") != expected_inputs
            for document in (self.bundle, self.coverage_document, self.inventory)
        ):
            raise WorkflowError("implementation artifacts do not bind frozen inputs")
        if not self.files or not self.coverage:
            raise WorkflowError("implementation bundle and translation coverage must be non-empty")
        worktree, changed, existing = self._repository_state()
        by_path = {item.path: item for item in self.files}
        if len(by_path) != len(self.files) or set(by_path) != changed:
            raise WorkflowError("declared implementation files differ from Git changed paths")
        contents = self._file_contents(worktree, by_path, existing)
        self._target_changes(by_path, existing)
        self._target_symbols()
        obligations = self._unsafe_obligations(contents)
        self._coverage(by_path, contents, obligations)

    def _repository_state(self) -> tuple[Path, set[str], set[str]]:
        repositories = _object(self.bundle.get("repositories"), "implementation repositories")
        target = _object(repositories.get("target_worktree"), "target worktree")
        worktree = (self.context.project_root / str(target.get("path", ""))).resolve()
        self._within(self.context.project_root, worktree, "target worktree")
        head = _git(worktree, "rev-parse", "HEAD^{commit}")
        if head != target.get("base_commit"):
            raise WorkflowError("target worktree HEAD differs from its frozen baseline")
        changed = set(filter(None, _git(worktree, "diff", "--name-only", "HEAD").splitlines()))
        changed.update(
            filter(None, _git(worktree, "ls-files", "--others", "--exclude-standard").splitlines())
        )
        existing = set(
            filter(None, _git(worktree, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
        )
        for baseline in repositories.get("baselines", []):
            item = _object(baseline, "repository baseline")
            root = (self.context.project_root / str(item.get("path", ""))).resolve()
            self._within(self.context.project_root, root, "repository baseline")
            if _git(root, "rev-parse", "HEAD^{commit}") != item.get("commit") or _git(
                root, "status", "--porcelain"
            ):
                raise WorkflowError("a frozen source or target baseline changed")
        return worktree, changed, existing

    def _file_contents(
        self,
        worktree: Path,
        by_path: dict[str, ImplementationFile],
        existing: set[str],
    ) -> dict[str, str]:
        contents = {}
        for relative, declared in by_path.items():
            path = (worktree / relative).resolve()
            self._within(worktree, path, "implementation file")
            data = path.read_bytes()
            content = data.decode("utf-8")
            if hashlib.sha256(data).hexdigest() != declared.sha256:
                raise WorkflowError(f"implementation content changed after freezing: {relative}")
            if relative not in existing and any(
                marker in content.casefold()
                for marker in (
                    "todo!",
                    "unimplemented!",
                    "todo",
                    "stub implementation",
                    "pending implementation",
                )
            ):
                raise WorkflowError(f"implementation contains unfinished code: {relative}")
            contents[relative] = content
        return contents

    def _target_changes(self, by_path: dict[str, ImplementationFile], existing: set[str]) -> None:
        plan = json_object(
            self.context.one_dependency(TargetStudyArtifact.CHANGE_PLAN)[1], "target change plan"
        )
        proposed = {
            str(item.get("change_id")): item
            for item in plan.get("proposed_preexisting_changes", [])
            if isinstance(item, dict)
        }
        inventory = self.inventory.get("target_changes", [])
        records = {str(item.get("path")): item for item in inventory if isinstance(item, dict)}
        modified = {path for path in by_path if path in existing}
        if set(records) != modified:
            raise WorkflowError("target change inventory differs from modified pre-existing files")
        required_level = self._required_change_level(plan)
        self._validate_change_level(required_level, records, modified)
        owned = tuple(PurePosixPath(path) for path in plan.get("driver_owned_paths", []))
        for path, declared in by_path.items():
            if path not in existing:
                if declared.role not in {
                    ImplementationFileRole.DRIVER,
                    ImplementationFileRole.PUBLIC_TEST,
                }:
                    raise WorkflowError(
                        "new implementation files must be driver or public-test owned"
                    )
                candidate = PurePosixPath(path)
                if not any(candidate == root or root in candidate.parents for root in owned):
                    raise WorkflowError(
                        f"new implementation path is outside the frozen driver-owned scope: {path}"
                    )
            elif declared.role is not ImplementationFileRole.INTEGRATION:
                raise WorkflowError("pre-existing target files must be declared as integration")
        for path, record in records.items():
            change_id = str(record.get("change_id"))
            planned = proposed.get(change_id)
            planned_paths = self._planned_change_paths(planned) if planned else ()
            candidate = PurePosixPath(path)
            if not any(
                candidate == planned_path or planned_path in candidate.parents
                for planned_path in planned_paths
            ):
                raise WorkflowError(
                    f"pre-existing target change lacks a frozen necessity record: {path}"
                )
            try:
                status = TargetChangeStatus(record.get("status"))
            except ValueError as error:
                raise WorkflowError("target change inventory has an invalid status") from error
            if status is TargetChangeStatus.BLOCKED:
                raise WorkflowError("blocked target change cannot finalize implementation")

    @staticmethod
    def _required_change_level(plan: dict[str, Any]) -> ChangeLevel:
        try:
            return ChangeLevel(plan.get("required_change_level"))
        except (TypeError, ValueError) as error:
            raise WorkflowError(
                "target change plan has an invalid required change level"
            ) from error

    @staticmethod
    def _validate_change_level(
        required: ChangeLevel,
        records: dict[str, Any],
        modified: set[str],
    ) -> None:
        if required is ChangeLevel.DRIVER_OWNED and modified:
            raise WorkflowError("driver-owned plan cannot modify pre-existing target files")
        if required is not ChangeLevel.DRIVER_OWNED and not records:
            raise WorkflowError("target change plan requires a pre-existing target change")

    @staticmethod
    def _planned_change_paths(change: dict[str, Any]) -> tuple[PurePosixPath, ...]:
        locators = change.get("exact_files_and_symbols")
        if not isinstance(locators, list) or not locators:
            raise WorkflowError("target change plan has invalid exact file and symbol locators")
        paths = []
        for locator in locators:
            if not isinstance(locator, str):
                raise WorkflowError("target change plan has invalid exact file and symbol locators")
            raw_path, separator, symbols = locator.partition(":")
            value = raw_path.strip().rstrip("/")
            path = PurePosixPath(value)
            if (
                not separator
                or not value
                or not symbols.strip()
                or path.is_absolute()
                or ".." in path.parts
                or path.as_posix() != value
            ):
                raise WorkflowError("target change plan has invalid exact file and symbol locators")
            paths.append(path)
        return tuple(paths)

    def _target_symbols(self) -> None:
        table = json_object(
            self.context.one_dependency(TargetStudyArtifact.API_EVIDENCE)[1], "target API evidence"
        )
        entries = {
            str(item.get("api_id")): item
            for item in table.get("entries", [])
            if isinstance(item, dict)
        }
        target_change_ids = {
            str(item.get("change_id"))
            for item in self.inventory.get("target_changes", [])
            if isinstance(item, dict)
        }
        symbols = self.inventory.get("target_symbols")
        if not isinstance(symbols, list) or not symbols:
            raise WorkflowError("implementation requires declared target symbols")
        for symbol in symbols:
            item = _object(symbol, "target symbol")
            entry = entries.get(str(item.get("api_id")))
            if entry is None:
                raise WorkflowError("target symbol is not backed by a resolved API entry")
            if (
                ApiConfidence(entry.get("confidence")) is ApiConfidence.UNKNOWN
                and entry.get("investigation_or_target_change_id") not in target_change_ids
            ):
                raise WorkflowError("target symbol is not backed by a current target change")

    def _unsafe_obligations(self, contents: dict[str, str]) -> set[str]:
        values = self.inventory.get("unsafe_obligations")
        if not isinstance(values, list):
            raise WorkflowError("unsafe obligations must be a list")
        identifiers = set()
        required = ("validity", "alignment", "lifetime", "exclusivity", "ordering")
        paths = set()
        for value in values:
            item = _object(value, "unsafe obligation")
            identifier = str(item.get("obligation_id", ""))
            if (
                not identifier
                or identifier in identifiers
                or not all(str(item.get(field, "")).strip() for field in required)
            ):
                raise WorkflowError("unsafe obligation is incomplete or duplicated")
            if not isinstance(item.get("evidence"), list) or not item["evidence"]:
                raise WorkflowError("unsafe obligation requires evidence")
            path = str(item.get("path", ""))
            self._target_span(
                {
                    "path": path,
                    "line_start": item.get("line_start"),
                    "line_end": item.get("line_end"),
                },
                contents,
            )
            identifiers.add(identifier)
            paths.add(path)
        unsafe_paths = {
            path
            for path, content in contents.items()
            if "unsafe" in content.split() or "unsafe " in content
        }
        if not unsafe_paths <= paths:
            raise WorkflowError("unsafe implementation has no local safety obligation")
        return identifiers

    def _coverage(
        self,
        by_path: dict[str, ImplementationFile],
        contents: dict[str, str],
        obligations: set[str],
    ) -> None:
        facts = json_object(
            self.context.one_dependency(SourceAnalysisArtifact.STRUCTURED_C_FACTS)[1],
            "structured C facts",
        )
        known_units = {str(unit["unit_id"]) for unit in facts.get("units", [])}
        covered_units: set[str] = set()
        contract_ids = self._ids(MigrationArtifact.CONTRACTS, "contracts", "id")
        tests = json_object(
            self.context.one_dependency(MigrationArtifact.TEST_PORT_MATRIX)[1], "test matrix"
        )
        required_tests = {
            str(test.get("test_id"))
            for test in tests.get("tests", [])
            if isinstance(test, dict)
            and test.get("disposition")
            in {TestDisposition.RETAIN.value, TestDisposition.ADAPT.value}
        }
        eligible_tests = {
            str(test.get("test_id"))
            for test in tests.get("tests", [])
            if isinstance(test, dict) and test.get("disposition") != TestDisposition.EXCLUDE.value
        }
        covered_tests = set()
        identifiers = set()
        for record in self.coverage:
            if (
                not record.identifier
                or record.identifier in identifiers
                or not record.lowering.strip()
            ):
                raise WorkflowError("translation coverage identity/lowering is incomplete")
            identifiers.add(record.identifier)
            if record.status not in {
                TranslationStatus.TRANSLATED,
                TranslationStatus.UNSAFE_REQUIRED,
            }:
                raise WorkflowError("translation coverage contains residual work")
            if record.status is TranslationStatus.UNSAFE_REQUIRED and (
                not record.safety_obligation_ids
                or not set(record.safety_obligation_ids) <= obligations
            ):
                raise WorkflowError("UNSAFE_REQUIRED coverage lacks complete obligations")
            if not set(record.contract_ids) <= contract_ids:
                raise WorkflowError("translation coverage references an unknown contract")
            self._target_span(record.target, contents)
            self._source_references(record)
            if not set(record.unit_ids) <= known_units:
                raise WorkflowError("translation coverage references an unknown source unit")
            covered_units.update(record.unit_ids)
            covered_tests.update(record.test_ids)
        if covered_units != known_units:
            raise WorkflowError("translation coverage omits a source translation unit")
        if not required_tests <= covered_tests or not covered_tests <= eligible_tests:
            raise WorkflowError(
                "translation coverage omits required tests or references excluded tests"
            )
        if covered_tests and not any(
            item.role is ImplementationFileRole.PUBLIC_TEST for item in by_path.values()
        ):
            raise WorkflowError("adapted public tests have no implementation file")
        if not any(item.role is ImplementationFileRole.DRIVER for item in by_path.values()):
            raise WorkflowError("implementation bundle has no Rust driver file")

    @staticmethod
    def _source_references(record: CoverageRecord) -> None:
        if record.domain is TranslationDomain.TEST_ASSERTION:
            if record.source_references:
                raise WorkflowError("test coverage cannot reference C source facts")
            return
        if {reference.unit_id for reference in record.source_references} != set(record.unit_ids):
            raise WorkflowError("coverage source references differ from its source units")
        for reference in record.source_references:
            span_ids = [str(span.get("fact_id", "")) for span in reference.source_spans]
            if set(span_ids) != set(reference.fact_ids) or any(
                not ({"loc", "range"} & span.keys()) for span in reference.source_spans
            ):
                raise WorkflowError("coverage source facts lack exact source spans")

    def _ids(self, kind: MigrationArtifact, collection: str, field: str) -> set[str]:
        document = json_object(self.context.one_dependency(kind)[1], kind.value)
        return {
            str(item.get(field)) for item in document.get(collection, []) if isinstance(item, dict)
        }

    @staticmethod
    def _target_span(value: dict[str, Any], contents: dict[str, str]) -> None:
        path = value.get("path")
        try:
            start, end = int(value["line_start"]), int(value["line_end"])
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("target span is invalid") from error
        if path not in contents or not (1 <= start <= end <= len(contents[path].splitlines())):
            raise WorkflowError("target span is outside the frozen implementation")

    @staticmethod
    def _within(root: Path, path: Path, label: str) -> None:
        if path != root and root not in path.parents:
            raise WorkflowError(f"{label} escapes the project workspace")


def validate_implementation_bundle(context: BundleValidationContext) -> None:
    DriverImplementationGate(context).validate()
