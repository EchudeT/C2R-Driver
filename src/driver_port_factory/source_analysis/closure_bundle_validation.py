from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..acquisition.contracts import AcquisitionArtifact
from ..acquisition.facets import EvidenceFacet, EvidenceLane, SourceFacet
from ..acquisition.frozen_checkout_validation import verify_git_checkout, verify_lock
from ..acquisition.material import GitBlobOrigin, MaterialRecord, parse_materials
from ..acquisition.repository_filesystem import git_bytes, git_output, workspace_file
from ..acquisition.repository_manifest import RepositoryAcquisition
from ..acquisition.repository_role import RepositoryRole
from ..core.models import WorkflowError
from ..core.validation import BundleValidationContext, json_object
from ..knowledge.corpus import CorpusManifest
from ..knowledge.index import KnowledgeIndex
from .contracts import SourceAnalysisArtifact
from .corpus_revision import SourceCorpusRevision

SOURCE_CLOSURE_FACET = EvidenceFacet(
    EvidenceLane.SOURCE,
    SourceFacet.DEPENDENCY_CLOSURE,
)


def validate_source_closure_bundle(context: BundleValidationContext) -> None:
    parent_ref, parent_data = context.one_dependency(AcquisitionArtifact.MATERIALS_MANIFEST)
    _, repository_data = context.one_dependency(AcquisitionArtifact.REPOSITORY_MANIFEST)
    successor_ref, successor_data = context.one_current(SourceAnalysisArtifact.MATERIALS_MANIFEST)
    _, revision_data = context.one_current(SourceAnalysisArtifact.KNOWLEDGE_REVISION)
    _, closure_data = context.one_current(SourceAnalysisArtifact.SOURCE_CLOSURE)
    _, compile_data = context.one_current(SourceAnalysisArtifact.COMPILE_MANIFEST)

    parent = parse_materials(parent_data)
    successor = parse_materials(successor_data)
    revision = SourceCorpusRevision.from_dict(
        json_object(revision_data, SourceAnalysisArtifact.KNOWLEDGE_REVISION.value)
    )
    acquisition = RepositoryAcquisition.from_dict(json.loads(repository_data))
    closure = json_object(closure_data, SourceAnalysisArtifact.SOURCE_CLOSURE.value)
    compile_manifest = json_object(compile_data, SourceAnalysisArtifact.COMPILE_MANIFEST.value)

    if revision.parent_manifest_sha256 != parent_ref.digest:
        raise WorkflowError("source corpus revision does not bind its acquisition manifest")
    if revision.manifest_sha256 != successor_ref.digest:
        raise WorkflowError("source corpus revision does not bind its successor manifest")
    if successor[: len(parent)] != parent:
        raise WorkflowError("source corpus revision changed or reordered its parent records")
    additions = successor[len(parent) :]
    if additions != revision.added_materials:
        raise WorkflowError("source corpus revision additions differ from its successor manifest")
    if revision.index_status.get("record_count") != len(successor):
        raise WorkflowError("source corpus revision index record count is inconsistent")
    candidate = CorpusManifest(
        successor,
        successor_data,
        successor_ref.digest,
        successor_ref.source,
    )
    if KnowledgeIndex(context.project_root, candidate).status() != revision.index_status:
        raise WorkflowError("source corpus revision does not bind its content-addressed index")

    source = acquisition.checkout(RepositoryRole.SOURCE)
    verify_lock(context.project_root, source)
    verify_git_checkout(context.project_root, source)
    if compile_manifest.get("source_revision") != source.resolved_commit:
        raise WorkflowError("compile manifest does not bind the frozen source revision")
    required_paths = _closure_paths(closure)
    parent_paths = set(_source_git_paths(parent, source.resolved_commit))
    addition_path_records = _source_git_paths(additions, source.resolved_commit)
    addition_paths = set(addition_path_records)
    if len(addition_paths) != len(addition_path_records):
        raise WorkflowError("source corpus additions contain duplicate source Git paths")
    if addition_paths != required_paths - parent_paths:
        raise WorkflowError(
            "source corpus additions do not exactly close the validated source paths"
        )
    if not required_paths <= parent_paths | addition_paths:
        raise WorkflowError("source corpus omits validated source closure paths")
    for material in additions:
        _verify_source_addition(
            context.project_root,
            source.checkout_path,
            source.resolved_commit,
            material,
        )


def _verify_source_addition(
    project_root: Path,
    checkout_path: str,
    revision: str,
    material: MaterialRecord,
) -> None:
    origin = material.origin
    if (
        material.facet != SOURCE_CLOSURE_FACET
        or not material.original
        or not material.index
        or not isinstance(origin, GitBlobOrigin)
        or origin.repository is not RepositoryRole.SOURCE
        or origin.commit != revision
    ):
        raise WorkflowError("source corpus addition is not a frozen source Git blob")
    checkout = project_root / checkout_path
    expected = (checkout / origin.path).resolve()
    material_path = workspace_file(project_root, material.path, "source corpus material")
    if material_path != expected:
        raise WorkflowError("source corpus material path differs from its Git origin")
    blob = git_output(
        project_root,
        "-C",
        str(checkout),
        "rev-parse",
        f"{revision}:{origin.path}",
    )
    if blob != origin.blob:
        raise WorkflowError("source corpus material blob identity drifted")
    data = git_bytes(project_root, "-C", str(checkout), "cat-file", "blob", blob)
    if (
        data != material_path.read_bytes()
        or len(data) != material.size_bytes
        or hashlib.sha256(data).hexdigest() != material.sha256
    ):
        raise WorkflowError("source corpus material bytes differ from the frozen Git blob")


def _closure_paths(closure: dict[str, object]) -> set[str]:
    paths: set[str] = set()
    units = closure.get("translation_units")
    if not isinstance(units, list):
        raise WorkflowError("source closure translation units are invalid")
    for unit in units:
        if not isinstance(unit, dict):
            raise WorkflowError("source closure translation unit is invalid")
        paths.add(_path(unit.get("source_path")))
        dependencies = unit.get("dependencies")
        if not isinstance(dependencies, list):
            raise WorkflowError("source closure dependencies are invalid")
        paths.update(_path(dependency.get("path")) for dependency in dependencies)
    categories = closure.get("closure_categories")
    if not isinstance(categories, dict):
        raise WorkflowError("source closure categories are invalid")
    for category in categories.values():
        if not isinstance(category, dict) or not isinstance(category.get("paths"), list):
            raise WorkflowError("source closure category paths are invalid")
        paths.update(_path(path) for path in category["paths"])
    for field in ("configuration_inputs", "generated_headers"):
        record = closure.get(field)
        if not isinstance(record, dict) or not isinstance(record.get("paths"), list):
            raise WorkflowError(f"source closure {field} paths are invalid")
        paths.update(_path(path) for path in record["paths"])
    return paths


def _source_git_paths(
    records: tuple[MaterialRecord, ...],
    revision: str,
) -> tuple[str, ...]:
    return tuple(
        record.origin.path
        for record in records
        if isinstance(record.origin, GitBlobOrigin)
        and record.origin.repository is RepositoryRole.SOURCE
        and record.origin.commit == revision
    )


def _path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise WorkflowError("source closure contains an invalid path")
    return value
