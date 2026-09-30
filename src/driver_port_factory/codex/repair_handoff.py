"""Bound triggering reports, without another model call or acceptance gate."""
import hashlib
import re

from ..core.models import WorkflowError


def repair_task(project, feedback, *, stage=None, budget=6000):
    ref = feedback.get("repair_report")
    if not ref:
        return None
    path = project.artifacts.path_for_digest(ref["digest"])
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != ref["digest"]:
        raise WorkflowError("Bound repair report integrity mismatch")
    text = data.decode("utf-8")
    complete = len(text) <= budget
    if complete:
        excerpt = text
    else:
        sections = [s for s in re.split(r"(?m)(?=^#{1,3} )", text) if s.strip()]
        # Explicit stage mentions only order evidence; they never classify or close findings.
        relevant = [s for s in sections if stage and stage in s]
        other = [s for s in sections if s not in relevant]
        if relevant and other:
            focused_budget = budget * 3 // 4
            selected = [s[:max(1, focused_budget // len(relevant))] for s in relevant]
            selected += [s[:max(1, (budget - focused_budget) // len(other))] for s in other]
        else:
            selected = [s[:max(1, budget // len(sections))] for s in sections]
        excerpt = "\n".join(selected)[:budget]
    return {
        "source": {**ref, "path": str(path)},
        "trigger": feedback.get("trigger"),
        "repair_root": feedback.get("repair_root"),
        "stage": stage,
        "report_excerpt": excerpt,
        "complete": complete,
        "instruction": (
            "This report triggered the current repair. Read it before old execution errors. "
            "If incomplete, use the bound source for omitted finding details. Address findings "
            "owned by this stage and preserve later-stage findings for their owners. Preserve "
            "unaffected work and run only affected checks. A repeated "
            "upstream PASS does not close these findings. In the existing work report, briefly "
            "state the correction or counterevidence and remaining obligations; no extra report "
            "or test matrix is required. Do not infer closure from unchanged files or receipts."
        ),
    }


def organize_repair_context(context, stage, known, supplied):
    """One report entry and one observation entry; dedup only the same stage view/thread."""
    result = dict(context)
    task = result.get("repair_task")
    if not task:
        return result
    task = dict(task)
    source = task["source"]
    # Remove navigation aliases only when their identity exactly matches this report.
    for key in ("analysis_review", "previous_review", "analysis_review_path",
                "runtime_review_path", "previous_work_report"):
        value = result.get(key)
        if (isinstance(value, dict) and value.get("digest") == source["digest"]
                or isinstance(value, str) and value == source["path"]):
            result.pop(key)
    state = result.pop("repair_state", None)
    if state:
        task["state"] = {k: v for k, v in state.items()
                         if k not in {"repair_report", "trigger", "repair_root", "reason"}}
    focus = result.pop("repair_focus", None)
    if focus:
        task["observations"] = {k: v for k, v in focus.items() if k != "instruction"}
        task["observations"]["authority"] = "Historical receipts, not current worktree measurements."
    # Stage and excerpt identity matter: another node must receive its own view even in
    # a shared worker thread. Fresh/reset sessions supply no known identity.
    identity = hashlib.sha256((stage + "\n" + source["digest"] + "\n"
                               + task["report_excerpt"]).encode()).hexdigest()
    key = "repair_view/" + stage
    if known.get(key) == identity:
        task.pop("report_excerpt")
        task.pop("complete")
        task["previously_supplied"] = True
        task["retrieval"] = "Same view previously supplied, not necessarily read or remembered; source remains available."
    supplied[key] = identity
    result["repair_task"] = task
    return result
