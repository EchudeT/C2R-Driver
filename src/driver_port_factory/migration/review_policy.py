"""Conservative review escalation signals, not a proof of semantic correctness."""
from __future__ import annotations

from pathlib import Path

from ..codex.contracts import CodexOutputError

SELF_PASS = "DPF_SELF_REVIEW: PASS"
REQUEST = "DPF_INDEPENDENT_REVIEW:"


def require_self_review(text: str) -> None:
    if not text.rstrip().endswith("\n" + SELF_PASS):
        raise CodexOutputError(
            "Complete the Skill's self-check in the existing work report and end it with "
            "DPF_SELF_REVIEW: PASS only after required current-stage checks pass. "
            "Do not create another report or review round. Request a same-phase prerequisite "
            "from context.repair_targets with DPF_REPAIR_STAGE then DPF_REVIEW: REWORK, "
            "or report a concrete blocker ending DPF_STATUS: BLOCKED. Earlier phases are "
            "sealed; never claim false PASS."
        )


def review_decision(root: Path, bundle: dict, reports: tuple[str, ...]) -> dict:
    """Use hash-bound source and explicit requests; no model-written risk table."""
    from .implementation import _git, validate_worktree_snapshot
    from .rust_risk import RiskAnalysisLimit, impacted_risks

    worktree = validate_worktree_snapshot(root, bundle)
    base = bundle["target_worktree"]["base_commit"]
    changed = {item["path"]: item for item in bundle["files"] if item["path"].endswith(".rs")}
    paths = {path for path in _git(worktree, "ls-tree", "-r", "--name-only", "-z", base).split("\0")
             if path.endswith(".rs")} if changed else set()
    before, after = {}, {}
    limit = None
    total_bytes = 0
    # Read unchanged Rust too: a changed safe helper can serve an unchanged unsafe caller.
    # Do not feed this index to the model; only bounded risk locations leave this function.
    for relative in sorted(paths | changed.keys()):
        item = changed.get(relative)
        path = worktree / relative
        if path.is_symlink():
            limit = "Rust symlink prevents complete local dependency inspection"
            break
        total_bytes += path.stat().st_size if path.is_file() else 0
        if total_bytes > 16 * 1024 * 1024:
            limit = "Rust sources exceed the 16 MiB lightweight analysis budget"
            break
        if item is None:
            before[relative] = after[relative] = path.read_bytes()
        else:
            if item["preexisting"]:
                total_bytes += int(_git(worktree, "cat-file", "-s", f"{base}:{relative}"))
                if total_bytes > 16 * 1024 * 1024:
                    limit = "Rust sources exceed the 16 MiB lightweight analysis budget"
                    break
                before[relative] = _git(worktree, "show", "-z", f"{base}:{relative}").encode()
            if item["state"] == "file":
                after[relative] = path.read_bytes()
    try:
        reasons = impacted_risks(before, after) if changed and limit is None else []
    except (RiskAnalysisLimit, RecursionError) as error:
        limit = str(error)
    if limit is not None:
        reasons = [{"kind": "unresolved_rust_impact", "paths": sorted(changed), "reason": limit}]
    for text in reports:
        for line in text.splitlines():
            if line.strip().startswith(REQUEST):
                reason = line.strip()[len(REQUEST):].strip()
                if reason:
                    entry = {"kind": "worker_request", "reason": reason}
                    if entry not in reasons:
                        reasons.append(entry)
    return {"policy": "rust-change-impact-or-explicit-request", "independent_required": bool(reasons),
            "reasons": reasons}
