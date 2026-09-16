from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
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
    SourceFactKind,
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

FACT_DOMAINS = {
    SourceFactKind.FUNCTION_DEFINITION: TranslationDomain.FUNCTION_CALLBACK,
    SourceFactKind.RECORD_DEFINITION: TranslationDomain.TYPE_LAYOUT,
    SourceFactKind.GLOBAL: TranslationDomain.GLOBAL_STATE,
    SourceFactKind.EFFECT: TranslationDomain.HARDWARE_EFFECT,
    SourceFactKind.CALL: TranslationDomain.LIFECYCLE_ERROR,
    SourceFactKind.CONTROL_FLOW: TranslationDomain.LIFECYCLE_ERROR,
}
INDEX_FACTS = {
    SourceFactKind.FUNCTION_DEFINITION: "function_definitions",
    SourceFactKind.RECORD_DEFINITION: "record_definitions",
    SourceFactKind.GLOBAL: "globals",
    SourceFactKind.EFFECT: "effects",
    SourceFactKind.CALL: "calls",
    SourceFactKind.CONTROL_FLOW: "control_flow",
}


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be an object")
    return value


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkflowError(f"{label} must be a string list")
    return tuple(value)


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

    def to_dict(self, content: str | None = None) -> dict[str, Any]:
        value = {"path": self.path, "role": self.role.value, "sha256": self.sha256}
        if content is not None:
            value["content"] = content
        return value


@dataclass(frozen=True, slots=True)
class SourceFact:
    unit_id: str
    kind: SourceFactKind
    node_id: str
    detail: str | None

    def key(self) -> tuple[str, SourceFactKind, str, str | None]:
        return self.unit_id, self.kind, self.node_id, self.detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "kind": self.kind.value,
            "node_id": self.node_id,
            "detail": self.detail,
        }


def _source_fact_inventory(
    project_root: Path,
    facts: dict[str, Any],
) -> dict[tuple[str, TranslationDomain], tuple[tuple[SourceFact, dict[str, Any]], ...]]:
    inventory: dict[
        tuple[str, TranslationDomain], list[tuple[SourceFact, dict[str, Any]]]
    ] = {}
    for unit in facts.get("units", []):
        unit_id = str(unit["unit_id"])
        path = (project_root / unit["semantic_index"]["path"]).resolve()
        if file_sha256(path) != unit["semantic_index"]["sha256"]:
            raise WorkflowError("structured semantic index changed")
        semantic = json.loads(path.read_text(encoding="utf-8"))
        nodes = {node["id"]: node for node in semantic["nodes"]}
        for kind, index_name in INDEX_FACTS.items():
            records = inventory.setdefault((unit_id, FACT_DOMAINS[kind]), [])
            for value in semantic["indexes"][index_name]:
                node_id = str(value["node_id"] if isinstance(value, dict) else value)
                detail = str(value["kind"]) if kind is SourceFactKind.EFFECT else None
                node = nodes[node_id]
                records.append(
                    (
                        SourceFact(unit_id, kind, node_id, detail),
                        {
                            "unit_id": unit_id,
                            "node_id": node_id,
                            "source_path": semantic["source_path"],
                            "loc": node.get("loc"),
                            "range": node.get("range"),
                        },
                    )
                )
    return {key: tuple(records) for key, records in inventory.items() if records}


@dataclass(frozen=True, slots=True)
class CoverageRecord:
    identifier: str
    domain: TranslationDomain
    unit_ids: tuple[str, ...]
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
    files: tuple[ImplementationFile, ...]
    coverage: tuple[CoverageRecord, ...]
    target_changes: tuple[dict[str, Any], ...]
    target_symbols: tuple[dict[str, Any], ...]
    unsafe_obligations: tuple[dict[str, Any], ...]

    @classmethod
    def read(cls, path: Path) -> ImplementationResponse:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("Codex implementation response is not UTF-8 JSON") from error
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise WorkflowError("implementation response must be a schema_version=1 object")
        try:
            collections = [
                value[name]
                for name in (
                    "files",
                    "coverage",
                    "target_changes",
                    "target_symbols",
                    "unsafe_obligations",
                )
            ]
            if not all(isinstance(items, list) for items in collections):
                raise TypeError
            return cls(
                tuple(ImplementationFile.from_dict(item) for item in collections[0]),
                tuple(CoverageRecord.from_dict(item) for item in collections[1]),
                tuple(_object(item, "target change") for item in collections[2]),
                tuple(_object(item, "target symbol") for item in collections[3]),
                tuple(_object(item, "unsafe obligation") for item in collections[4]),
            )
        except (KeyError, TypeError) as error:
            raise WorkflowError("implementation response has an invalid typed boundary") from error


class DriverImplementationService:
    def finalize(self, project: Project, response: ImplementationResponse) -> None:
        if project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status is not StageStatus.RUNNING:
            raise WorkflowError("driver_implementation must be RUNNING")
        acquisition = load_repository_acquisition(project)
        worktree = (project.root / acquisition.target_worktree.path).resolve()
        files = []
        for declared in response.files:
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
                content = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise WorkflowError(f"implementation file is not UTF-8: {declared.path}") from error
            if hashlib.sha256(data).hexdigest() != declared.sha256:
                raise WorkflowError(f"implementation file hash differs: {declared.path}")
            files.append(declared.to_dict(content))
        inputs = self._inputs(project)
        fact_inventory = _source_fact_inventory(
            project.root,
            project.load_json_artifact(
                SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
                SourceAnalysisArtifact.STRUCTURED_C_FACTS,
            ),
        )
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
                    "coverage": [
                        self._expanded_coverage(record, fact_inventory)
                        for record in response.coverage
                    ],
                },
            ),
            (
                MigrationArtifact.TARGET_CHANGE_INVENTORY,
                {
                    "schema_version": 1,
                    "inputs": inputs,
                    "target_changes": list(response.target_changes),
                    "target_symbols": list(response.target_symbols),
                    "unsafe_obligations": list(response.unsafe_obligations),
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
    def _expanded_coverage(
        record: CoverageRecord,
        inventory: dict[
            tuple[str, TranslationDomain], tuple[tuple[SourceFact, dict[str, Any]], ...]
        ],
    ) -> dict[str, Any]:
        selected: list[tuple[SourceFact, dict[str, Any]]] = []
        for unit_id in record.unit_ids:
            selected.extend(inventory.get((unit_id, record.domain), ()))
        result = record.to_dict()
        result["source_facts"] = [fact.to_dict() for fact, _span in selected]
        result["source_spans"] = [span for _fact, span in selected]
        return result

    @staticmethod
    def _inputs(project: Project) -> dict[str, Any]:
        dependencies = project.workflow.spec(MigrationStage.DRIVER_IMPLEMENTATION).dependencies
        result = {}
        for kind in IMPLEMENTATION_INPUTS:
            matches = [
                ref
                for stage in dependencies
                for ref in project.artifact_refs(stage=stage)
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
        contents = self._file_contents(worktree, by_path)
        self._target_changes(by_path, existing)
        self._target_symbols()
        obligations = self._unsafe_obligations(contents)
        self._coverage(by_path, contents, obligations)

    def _repository_state(self) -> tuple[Path, set[str], set[str]]:
        repositories = _object(self.bundle.get("repositories"), "implementation repositories")
        target = _object(repositories.get("target_worktree"), "target worktree")
        worktree = (self.context.project_root / str(target.get("path", ""))).resolve()
        self._within(self.context.project_root, worktree, "target worktree")
        head = self._git(worktree, "rev-parse", "HEAD^{commit}")
        if head != target.get("base_commit"):
            raise WorkflowError("target worktree HEAD differs from its frozen baseline")
        changed = set(filter(None, self._git(worktree, "diff", "--name-only", "HEAD").splitlines()))
        changed.update(
            filter(
                None, self._git(worktree, "ls-files", "--others", "--exclude-standard").splitlines()
            )
        )
        existing = set(
            filter(None, self._git(worktree, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
        )
        for baseline in repositories.get("baselines", []):
            item = _object(baseline, "repository baseline")
            root = (self.context.project_root / str(item.get("path", ""))).resolve()
            self._within(self.context.project_root, root, "repository baseline")
            if self._git(root, "rev-parse", "HEAD^{commit}") != item.get("commit") or self._git(
                root, "status", "--porcelain"
            ):
                raise WorkflowError("a frozen source or target baseline changed")
        return worktree, changed, existing

    def _file_contents(
        self, worktree: Path, by_path: dict[str, ImplementationFile]
    ) -> dict[str, str]:
        contents = {}
        for relative, declared in by_path.items():
            path = (worktree / relative).resolve()
            self._within(worktree, path, "implementation file")
            data = path.read_bytes()
            content = data.decode("utf-8")
            frozen = next(item for item in self.bundle["files"] if item["path"] == relative)
            if (
                hashlib.sha256(data).hexdigest() != declared.sha256
                or frozen.get("content") != content
            ):
                raise WorkflowError(f"implementation content changed after freezing: {relative}")
            if any(
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
        if plan.get("required_change_level") == ChangeLevel.DRIVER_OWNED.value and modified:
            raise WorkflowError("driver-owned plan cannot modify pre-existing target files")
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
            exact = planned.get("exact_files_and_symbols") if planned else None
            files = exact.get("files") if isinstance(exact, dict) else None
            if not planned or not isinstance(files, list) or path not in files:
                raise WorkflowError(
                    f"pre-existing target change lacks a frozen necessity record: {path}"
                )
            try:
                status = TargetChangeStatus(record.get("status"))
            except ValueError as error:
                raise WorkflowError("target change inventory has an invalid status") from error
            if status is TargetChangeStatus.BLOCKED:
                raise WorkflowError("blocked target change cannot finalize implementation")

    def _target_symbols(self) -> None:
        table = json_object(
            self.context.one_dependency(TargetStudyArtifact.API_EVIDENCE)[1], "target API evidence"
        )
        entries = {
            str(item.get("api_id")): item
            for item in table.get("entries", [])
            if isinstance(item, dict)
        }
        symbols = self.inventory.get("target_symbols")
        if not isinstance(symbols, list) or not symbols:
            raise WorkflowError("implementation requires declared target symbols")
        for symbol in symbols:
            item = _object(symbol, "target symbol")
            entry = entries.get(str(item.get("api_id")))
            if entry is None or ApiConfidence(entry.get("confidence")) is ApiConfidence.UNKNOWN:
                raise WorkflowError("target symbol is not backed by a resolved API entry")
            if item.get("symbol") != entry.get("api_or_type") or any(
                item.get(name) != entry.get(name)
                for name in ("definition_evidence", "call_site_evidence")
            ):
                raise WorkflowError(
                    "target symbol differs from frozen definition/call-site evidence"
                )

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
        inventory = _source_fact_inventory(self.context.project_root, facts)
        expected = {
            fact.key()
            for records in inventory.values()
            for fact, _span in records
        }
        actual = set()
        contract_ids = self._ids(MigrationArtifact.CONTRACTS, "contracts", "id")
        tests = json_object(
            self.context.one_dependency(MigrationArtifact.TEST_PORT_MATRIX)[1], "test matrix"
        )
        expected_tests = {
            str(test.get("test_id"))
            for test in tests.get("tests", [])
            if isinstance(test, dict)
            and test.get("disposition")
            in {TestDisposition.RETAIN.value, TestDisposition.ADAPT.value}
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
            selected = [
                inventory.get((unit_id, record.domain))
                for unit_id in record.unit_ids
            ]
            if any(records is None for records in selected):
                raise WorkflowError("translation coverage references an unknown unit/domain scope")
            keys = {
                fact.key()
                for records in selected
                if records is not None
                for fact, _span in records
            }
            actual.update(keys)
            covered_tests.update(record.test_ids)
        if actual != expected:
            raise WorkflowError(
                "translation coverage does not exactly cover structured source facts"
            )
        if covered_tests != expected_tests:
            raise WorkflowError(
                "translation coverage does not exactly cover retained/adapted public tests"
            )
        if expected_tests and not any(
            item.role is ImplementationFileRole.PUBLIC_TEST for item in by_path.values()
        ):
            raise WorkflowError("adapted public tests have no implementation file")
        if not any(item.role is ImplementationFileRole.DRIVER for item in by_path.values()):
            raise WorkflowError("implementation bundle has no Rust driver file")

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

    @staticmethod
    def _git(root: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments], text=True, capture_output=True, check=False
        )
        if completed.returncode:
            raise WorkflowError(f"Git inspection failed: {completed.stderr.strip()}")
        return completed.stdout.strip()


def validate_implementation_bundle(context: BundleValidationContext) -> None:
    DriverImplementationGate(context).validate()
