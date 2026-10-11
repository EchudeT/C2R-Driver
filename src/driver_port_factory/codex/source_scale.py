"""Optional analysis advice from the local source build unit, never a planning gate."""

import re
from pathlib import Path

LARGE_DRIVER_C_LINES = 10_000


def build_unit_files(root: Path, entry: str) -> tuple[Path, ...]:
    root = root.resolve()
    path = (root / entry).resolve()
    if not path.is_relative_to(root):
        return ()
    if path.is_dir():
        return tuple(sorted(p for p in path.rglob("*.c") if p.resolve().is_relative_to(root)))
    if not path.is_file() or path.suffix != ".c":
        return ()
    build = next(
        (path.parent / n for n in ("Kbuild", "Makefile") if (path.parent / n).is_file()), None
    )
    if build is None:
        return (path,)
    groups = {}
    text = build.read_text().replace("\\\n", " ")
    for line in text.splitlines():
        match = re.match(
            r"\s*([\w-]+)-(?:objs|y|m|\$\([^)]+\))\s*[:+?]?=\s*(.*)", line.split("#", 1)[0]
        )
        if match and match[1] != "obj":
            groups.setdefault(match[1], set()).update(
                re.findall(r"(?<![\w$/])([\w/-]+)\.o\b", match[2])
            )
    members = {path.stem}
    while True:
        expanded = members | {
            member
            for owner, children in groups.items()
            if owner in members or children & members
            for member in {owner, *children}
        }
        if expanded == members:
            break
        members = expanded
    return tuple(
        sorted(
            {path}
            | {
                (path.parent / (member + ".c")).resolve()
                for member in members
                if (path.parent / (member + ".c")).is_file()
                and (path.parent / (member + ".c")).resolve().is_relative_to(root)
            }
        )
    )


def measure(root: Path, entry: str) -> dict | None:
    files = build_unit_files(root, entry)
    if not files:
        return None
    counts = {}
    for path in files:
        data = path.read_bytes()
        counts[str(path.relative_to(root.resolve()))] = data.count(b"\n") + int(
            bool(data) and not data.endswith(b"\n")
        )
    return {
        "source_entry": entry,
        "c_physical_lines": sum(counts.values()),
        "c_files": counts,
        "metric": "C physical lines, including comments and blanks",
    }


def advice(size: dict | None) -> dict | None:
    if size is None or size["c_physical_lines"] < LARGE_DRIVER_C_LINES:
        return None
    return {
        **size,
        "threshold_c_lines": LARGE_DRIVER_C_LINES,
        "planning_advice": (
            "This source build unit is large. Prefer at least two meaningful implementation "
            "work packages for the frozen scope, each delivering a substantial capability. "
            "Packages may depend on earlier capabilities; shared state does not require merging "
            "them. Keep initialization, failure handling and cleanup with their owning capability. "
            "Preserve B/C/R/P and explicit dependencies. Do not split by file, function, test or "
            "route step, expand scope, or add reports. This is advice, not a minimum-count gate: "
            "if the scoped work is small or truly inseparable, keep one package. The measured "
            "source family may include excluded features and is not translated code size."
        ),
    }


def context(project) -> dict | None:
    from ..acquisition.repository import load_repository_acquisition
    from ..acquisition.repository_role import RepositoryRole
    from ..intake.contracts import IntakeArtifact, IntakeStage

    acquisition = load_repository_acquisition(project)
    source = acquisition.checkout(RepositoryRole.SOURCE)
    envelope = project.load_json_artifact(
        IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
    )
    root = project.root / source.checkout_path
    try:
        return advice(measure(root, envelope["source_driver_entry_or_repository_hint"]))
    except OSError:
        # Missing optional size evidence must not create another analysis/recovery gate.
        return None
