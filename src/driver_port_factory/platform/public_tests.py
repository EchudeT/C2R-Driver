"""Operator-provided public assertions. Workers adapt calls, not test expectations."""

import json
import shlex
from pathlib import Path

from ..core.models import WorkflowError
from ..intake.behavior_scope import effective
from .profile import digest

ASSETS = Path(__file__).parents[1] / "data/public-tests/pvpanic"
DIRECTORY = ".dpf-output/harness/public"


def assets(project):
    return (
        ASSETS
        if project.config.driver_name == "pvpanic-pci"
        else ASSETS.parent / project.config.driver_name
    )


def selected(project):
    if project is None:
        return False
    from .native import binding

    if binding(project) is not None:
        return True
    config = project.config
    scope = effective(config)
    return (
        config.managed_platform
        and config.source_platform.lower() == "linux"
        and config.target_platform.lower() == "asterinas"
        and config.driver_name in {"pvpanic-pci", "evbug", "ne2k-pci"}
        and scope["mode"] == "source-driver"
        and scope["integration"] == "target-kernel"
    )


def definition(project):
    from . import native

    if native.binding(project) is not None:
        return native.definition(project)
    from .guest import validate_case
    from .service import command

    rows = []
    directory = assets(project)
    files = {f"{DIRECTORY}/INTERFACE.md": (directory / "INTERFACE.md").read_text()}
    if project.config.driver_name == "evbug":
        files["kernel/core/comps/input/src/dpf_evbug_public.rs"] = (
            directory / "evbug_public.rs"
        ).read_text()
    if project.config.driver_name == "ne2k-pci":
        files["kernel/core/comps/network/src/dpf_ne2k_public.rs"] = (
            directory / "ne2k_public.rs"
        ).read_text()
    for item in json.loads((directory / "cases.json").read_text()):
        case = validate_case(item["case"])
        name = f"{DIRECTORY}/{item['id']}"
        files[name + ".json"] = json.dumps(case, indent=2) + "\n"
        files[name + ".sh"] = (
            "#!/bin/sh\nset -eu\nexec "
            + shlex.join(command(project, "run-case") + [name + ".json"])
            + "\n"
        )
        rows.append(
            {
                "id": item["id"],
                "script": name + ".sh",
                "timeout_seconds": case["timeout_seconds"] + 60,
            }
        )
    return files, rows


def install(project, worktree):
    if not selected(project):
        return
    files, rows = definition(project)
    binding = project.control / "public-tests.json"
    stamp = {"id": project.config.driver_name + "-public-v1", "definition": digest([files, rows])}
    if binding.exists():
        verify(project, worktree)
        return
    manifest = worktree / ".dpf-output/experiments.json"
    current = json.loads(manifest.read_text()) if manifest.exists() else []
    if not isinstance(current, list) or any(r["id"] in {v["id"] for v in rows} for r in current):
        raise WorkflowError("Public test installation conflicts with existing cases")
    for name, text in files.items():
        path = worktree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if path.suffix == ".sh":
            path.chmod(0o755)
    manifest.write_text(json.dumps([*current, *rows], indent=2) + "\n")
    binding.write_text(json.dumps(stamp, indent=2) + "\n")


def verify(project, worktree, *, final=False):
    if not selected(project):
        return
    files, rows = definition(project)
    from ..migration.experiments import passed_case

    if not final and all(passed_case(project, row["id"]) is not None for row in rows):
        return  # Completed acceptance is retained; changes do not reopen passed tests.
    binding = project.control / "public-tests.json"
    if not binding.is_file() or json.loads(binding.read_text()).get("definition") != digest(
        [files, rows]
    ):
        raise WorkflowError("Prepared public test definition is missing or changed")
    for name, text in files.items():
        path = worktree / name
        if not path.is_file() or path.is_symlink() or path.read_text() != text:
            raise WorkflowError(f"Prepared public assertion changed or disappeared: {name}")
    manifest = worktree / ".dpf-output/experiments.json"
    current = json.loads(manifest.read_text()) if manifest.is_file() else []
    if not isinstance(current, list):
        raise WorkflowError("Prepared public tests require their complete case manifest")
    if len(current) != len(rows) or {r.get("id") for r in current if isinstance(r, dict)} != {
        r["id"] for r in rows
    }:
        raise WorkflowError(
            "Prepared public case set changed: restore the operator-provided cases; "
            "analysis cannot add mandatory tests"
        )
    for row in rows:
        matches = [r for r in current if isinstance(r, dict) and r.get("id") == row["id"]]
        if len(matches) != 1 or any(matches[0].get(k) != v for k, v in row.items()):
            raise WorkflowError(f"Required public case missing or redirected: {row['id']}")
        if set(matches[0]) - (row.keys() | {"contracts"}):
            raise WorkflowError("Prepared cases cannot override dependencies or environment")


def context(project):
    if not selected(project):
        return None
    from . import native

    native_config = native.binding(project)
    rows = (
        native.definition(project)[1]
        if native_config is not None
        else json.loads((assets(project) / "cases.json").read_text())
    )
    return {
        "interface": f"{DIRECTORY}/INTERFACE.md",
        "cases": [row["id"] for row in rows],
        "rule": "Public stimuli and assertions are fixed. Read the interface once; "
        "adapt it to real driver operations, implement the driver and run driver_checks.check. "
        "Do not author/re-register these tests, change their assertions or write suite wrappers. "
        "Cases are not work packages. Source scope remains full native target integration. "
        "These case IDs are the complete required runtime check set for this run, shared by "
        "implementation and final acceptance. Analysis prose cannot add mandatory executable "
        "tests. For uncovered source obligations, give a bounded source argument and validation "
        "limits; a concrete violation or unresolved correctness-critical premise still requires "
        "repair or a blocker. Missing exhaustive coverage does not require ktests, "
        "mock frameworks or fault injection. No duplicate test matrix is needed per package.",
    }
