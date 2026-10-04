"""Optional Asterinas component wiring, derived from an explicit current workspace example."""

import difflib
import json
import re
import tomllib

from ..core.models import WorkflowError
from . import service

NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def path_in(root, relative):
    if not isinstance(relative, str) or not relative:
        raise WorkflowError("Scaffold path must be a nonempty relative path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise WorkflowError("Scaffold paths must stay in the target worktree")
    return path


def add_entry(text, table, entry):
    header = re.search(r"(?m)^\[" + re.escape(table) + r"\]\s*$", text)
    if not header:
        raise WorkflowError(f"Scaffold needs existing [{table}]; inspect integration example")
    return text[: header.end()] + "\n" + entry + text[header.end() :]


def add_member(text, key, member):
    # This adapter supports literal arrays in the workspace table; do not guess TOML layouts.
    header = re.search(r"(?m)^\[workspace\]\s*$", text)
    if not header:
        raise WorkflowError("Scaffold needs a workspace table")
    end = re.search(r"(?m)^\[", text[header.end() :])
    stop = header.end() + end.start() if end else len(text)
    segment = text[header.end() : stop]
    array = re.search(r"(?m)^" + re.escape(key) + r"\s*=\s*\[([^\]]*)\]", segment)
    if not array:
        raise WorkflowError(f"Scaffold needs literal workspace.{key} array")
    at = header.end() + array.end() - 1
    # Insert at array start, so trailing comments/comma style cannot break an existing last item.
    start = header.end() + array.start(1)
    return text[:start] + "\n    " + json.dumps(member) + "," + text[start:at] + text[at:]


def validate_names(package, template, dependencies):
    if not isinstance(package, str) or not NAME.fullmatch(package):
        raise WorkflowError("Component package must be a short Rust package name")
    if not isinstance(dependencies, (list, tuple)) or any(
        not isinstance(n, str) or not NAME.fullmatch(n) for n in dependencies
    ):
        raise WorkflowError("dependencies must list existing workspace package names")
    if not isinstance(template, str) or not NAME.fullmatch(template):
        raise WorkflowError("template must name an existing component")


def plan(root, package, template, dependencies=(), owner_source="kernel/core/src/init.rs"):
    """Read inputs and plan edits; unsupported layouts cause no partial writes."""
    validate_names(package, template, dependencies)
    root = root.resolve()
    source = path_in(root, owner_source)
    # Owner location is explicit; default is the known Asterinas core assembler.
    owner = source.parent
    while owner != root and not (owner / "Cargo.toml").is_file():
        owner = owner.parent
    if owner == root or not source.is_file():
        raise WorkflowError("Select an existing owner Rust source below its crate manifest")
    paths = [
        path_in(root, "Cargo.toml"),
        path_in(root, "Components.toml"),
        path_in(root, str((owner / "Cargo.toml").relative_to(root))),
        source,
    ]
    before = {p: p.read_text() for p in paths}
    workspace = tomllib.loads(before[paths[0]])["workspace"]
    known = workspace.get("dependencies", {})
    example = known.get(template)
    if not isinstance(example, dict) or not isinstance(example.get("path"), str):
        raise WorkflowError("template must name an existing workspace path dependency")
    template_root = path_in(root, example["path"])
    template_manifest = tomllib.loads(path_in(root, example["path"] + "/Cargo.toml").read_text())
    if "component" not in template_manifest.get("dependencies", {}):
        raise WorkflowError("Selected template does not use component initialization")
    names = list(dict.fromkeys(["component", *dependencies]))
    if package in known or any(n not in known for n in names):
        raise WorkflowError("Package already exists or requested workspace dependency is unknown")
    destination = path_in(root, str((template_root.parent / package).relative_to(root)))
    if destination.exists():
        raise WorkflowError("Component destination already exists; continue implementation there")
    relative = destination.relative_to(root).as_posix()
    if "edition" not in workspace.get("package", {}) or "lints" not in workspace:
        raise WorkflowError("Scaffold needs inherited workspace edition and lints")
    changed = dict(before)
    changed[paths[0]] = _workspace(before[paths[0]], workspace, example["path"], package, relative)
    changed[paths[1]] = add_entry(
        before[paths[1]], "components", f"{package} = {{ name = {json.dumps(package)} }}"
    )
    changed[paths[2]] = add_entry(before[paths[2]], "dependencies", f"{package}.workspace = true")
    changed[source] = (
        before[source]
        + f"\n// Retain the component initializer.\nuse {package.replace('-', '_')} as _;\n"
    )
    changed[destination / "Cargo.toml"] = (
        f'[package]\nname = {json.dumps(package)}\nversion = "0.1.0"\nedition.workspace = true\n'
        + "\n[dependencies]\n"
        + "\n".join(f"{n}.workspace = true" for n in names)
        + "\n\n[lints]\nworkspace = true\n"
    )
    changed[destination / "src/lib.rs"] = (
        "// SPDX-License-Identifier: MPL-2.0\n#![no_std]\n#![deny(unsafe_code)]\n"
        "extern crate alloc;\n\n#[component::init_component]\n"
        "fn init() -> Result<(), component::ComponentInitError> {\n"
        '    todo!("Implement the selected behavior before building/running");\n}\n'
    )
    for path, text in changed.items():
        if path.suffix == ".toml":
            tomllib.loads(text)
    return before, changed


def _workspace(text, workspace, template, package, relative):
    if template not in workspace.get("members", []):
        raise WorkflowError("Template is not a literal workspace member")
    for key in ("members", "default-members"):
        if template in workspace.get(key, []):
            text = add_member(text, key, relative)
    return add_entry(
        text, "workspace.dependencies", f"{package} = {{ path = {json.dumps(relative)} }}"
    )


def apply(before, changes):
    # Verify before mutation; rollback only this operation on a filesystem write failure.
    for path, text in changes.items():
        if path in before:
            if path.read_text() != before[path]:
                raise WorkflowError("Scaffold input changed before writing")
        elif path.exists():
            raise WorkflowError("Scaffold refuses to overwrite an existing file")
    written = []
    created = []
    try:
        for path, text in changes.items():
            missing = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                created.append(directory)
            written.append(path)
            path.write_text(text)
    except OSError:
        for path in reversed(written):
            if path in before:
                path.write_text(before[path])
            else:
                path.unlink(missing_ok=True)
        for directory in reversed(created):
            directory.rmdir()
        raise


def run(project, package, template, *, dependencies=(), owner_source="kernel/core/src/init.rs"):
    service.active(project)
    service.verified(project)
    with service.locked(project):
        _, root = service.load(project)
        try:
            before, changes = plan(root, package, template, dependencies, owner_source)
            apply(before, changes)
        except (OSError, KeyError, ValueError) as error:
            raise WorkflowError(f"Scaffold unavailable for this layout: {error}") from error
        return {
            "status": "SCAFFOLDED_NOT_IMPLEMENTED",
            "package": package,
            "files": [str(p.relative_to(root)) for p in changes],
            "diff": "".join(
                "".join(
                    difflib.unified_diff(
                        before.get(p, "").splitlines(True),
                        text.splitlines(True),
                        fromfile=str(p.relative_to(root)),
                        tofile=str(p.relative_to(root)),
                    )
                )
                for p, text in changes.items()
            ),
            "next": "Fill the todo initializer and current behavior, then format/build and verify "
            "actual linkage/device behavior before progress done. No driver logic, PCI IDs, "
            "MMIO operations or acceptance claims are generated.",
        }
