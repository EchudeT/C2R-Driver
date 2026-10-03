"""On-demand component wiring examples from the current target, never a delivery verdict."""

import re
import tomllib
from pathlib import Path

from ..core.models import WorkflowError
from ..knowledge.index import file_sha256
from . import service

GUIDE = Path(__file__).resolve().parents[1] / "data/component-integration.md"


def _read(worktree, relative):
    path = (worktree / relative).resolve()
    if not path.is_relative_to(worktree) or not path.is_file():
        raise WorkflowError(f"Component example unavailable in this target: {relative}")
    return path


def _excerpt(worktree, path, pattern, limit=3):
    lines = path.read_text().splitlines()
    matches = [i for i, line in enumerate(lines) if re.search(pattern, line)][:limit]
    return {
        "path": str(path.relative_to(worktree)),
        "sha256": file_sha256(path),
        "excerpts": [
            {
                "line": i + 1,
                "text": "\n".join(lines[max(0, i - 1) : i + 5]),
                "start_line": max(0, i - 1) + 1,
            }
            for i in matches
        ],
    }


def example(project, package=None):
    service.verified(project)
    profile, worktree = service.load(project)
    manifest = _read(worktree, "Cargo.toml")
    components = _read(worktree, "Components.toml")
    workspace = tomllib.loads(manifest.read_text()).get("workspace", {})
    dependencies = workspace.get("dependencies", {})
    known = {
        k: v["path"]
        for k, v in dependencies.items()
        if isinstance(v, dict) and isinstance(v.get("path"), str)
    }
    result = {
        "scope": "Current-target source navigation; not a tested new component or acceptance",
        "target_revision": profile["target_revision"],
        "guide": GUIDE.read_text(),
    }
    if package is None:
        result["packages"] = list(known)[:16]
        result["next"] = (
            "Select one relevant existing component package; pass package to this tool."
        )
        return result
    if not isinstance(package, str) or package not in known:
        raise WorkflowError("Select an existing workspace path-dependency package")
    component = _read(worktree, known[package] + "/Cargo.toml")
    entry = _read(worktree, known[package] + "/src/lib.rs")
    escaped = re.escape(package)
    evidence = [
        _excerpt(worktree, manifest, escaped),
        _excerpt(worktree, components, escaped),
        _excerpt(worktree, component, r"name\s*=|component.*=", limit=4),
        _excerpt(worktree, entry, r"#\[init_component|fn .*init|use component", limit=4),
    ]
    # Only direct consumers, bounded examples. No full source dump or API survey.
    consumers = []
    for member in workspace.get("members", []):
        if not isinstance(member, str) or "*" in member:
            continue
        path = _read(worktree, member + "/Cargo.toml")
        data = tomllib.loads(path.read_text())
        if package not in data.get("dependencies", {}):
            continue
        evidence.append(_excerpt(worktree, path, escaped))
        references = []
        for source in sorted((path.parent / "src").rglob("*.rs")):
            if source.is_symlink() or not source.resolve().is_relative_to(worktree):
                continue
            snippet = _excerpt(
                worktree, source, r"\b" + re.escape(package.replace("-", "_")) + r"\b"
            )
            if snippet["excerpts"]:
                references.append(snippet)
            if len(references) == 2:
                break
        consumers.append(
            {"manifest": str(path.relative_to(worktree)), "rust_references": references}
        )
        if len(consumers) == 2:
            break
    result.update(package=package, evidence=evidence, consumers=consumers)
    result["limits"] = (
        "Examples are bounded current source excerpts with identities; missing references "
        "are not proof of absence. Generated/conditional linkage and initialization order "
        "may need a focused lookup. This tool does not modify or approve code."
    )
    return result
