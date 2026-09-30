"""Small evidence navigation and repair deltas, without model calls or new gates."""

from .observation_handoff import observation_handoff

# These are priorities, not additional required artifacts or acceptance checks.
PRIORITIES = {
    "target_platform_study": ("repository_manifest", "artifact_mode_record", "kb_query_contract"),
    "migration_contracts": ("target_study_report", "migration_handoff", "kb_query_contract"),
    "target_framework_enablement": ("migration_contracts", "target_study_report",
                                    "test_port_matrix", "analysis_review_report"),
    "driver_implementation": ("migration_contracts", "test_port_matrix",
                              "target_framework_enablement_report", "target_study_report"),
    "artifact_preparation": ("compliance_report", "target_change_inventory", "migration_contracts"),
    "public_qemu_validation": ("test_port_matrix", "artifact_identity", "compliance_report"),
}


def references(value, location=()):
    if isinstance(value, dict):
        if all(isinstance(value.get(k), str) for k in ("kind", "digest", "path")):
            yield "/".join(location), value
        else:
            for key, child in value.items():
                yield from references(child, (*location, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from references(child, (*location, str(index)))


def reading_plan(stage, context, *, limit=8):
    priorities = PRIORITIES.get(stage.value, ())
    changes = context.get("input_changes", {})
    changed = set(changes.get("changed", ()))
    unchanged = set(changes.get("unchanged", ()))
    candidates = {}
    aliases = {}
    for location, ref in references(context):
        status = "changed" if location in changed else (
            "unchanged" if location in unchanged else "new_or_untracked")
        rank = priorities.index(ref["kind"]) if ref["kind"] in priorities else len(priorities)
        score = (0 if status == "changed" else 1, rank, status == "unchanged", location)
        identity = ref["digest"]
        aliases.setdefault(identity, set()).add(ref["kind"])
        if identity not in candidates or score < candidates[identity][0]:
            candidates[identity] = (score, {"location": location, "kind": ref["kind"],
                                          "input_status": status})
    for identity, (_, item) in candidates.items():
        if len(aliases[identity]) > 1:
            item["also_supplies"] = sorted(aliases[identity] - {item["kind"]})
    ordered = sorted(candidates.values(), key=lambda item: item[0])
    first = [item for _, item in ordered
             if item["input_status"] == "changed" or item["kind"] in priorities][:limit]
    if not first:
        return None
    return {
        "first": first, "other_unique_references": len(candidates) - len(first),
        "instruction": "Locations address the supplied context; all original references remain "
        "available. Read the handoff first when supplied, then relevant sections at these "
        "locations. also_supplies names other artifact roles with identical content; read once "
        "but cover the obligations of every role. Start from current conclusions and retrieve "
        "changed evidence before relying on old conclusions. Unchanged means "
        "previously supplied, not previously read or verified. Retrieve other material only for "
        "a concrete question; do not open binary bundles as text or reread every linked report. "
        "This order is advisory, not a list of required or sufficient evidence.",
    }


def compact_feedback(project, context, *, threshold=6000):
    """Keep exact lengthy feedback in CAS; never truncate the only copy of a finding."""
    result = dict(context)
    for key in ("controller_execution", "controller_feedback", "checker_findings"):
        value = context.get(key)
        if not isinstance(value, str) or len(value) <= threshold:
            continue
        artifact = project.artifacts.put_bytes(value.encode(), kind="controller_feedback")
        result[key] = {
            "full_record": {"kind": "controller_feedback", "digest": artifact.digest,
                            "path": str(project.artifacts.path_for_digest(artifact.digest))},
            "excerpt": value[:1500] + "\n[... excerpt omitted ...]\n" + value[-1000:],
            "characters": len(value),
            "instruction": "This excerpt is incomplete. Search/read the full record for the "
                           "specific failure before repairing or accepting it; omitted findings "
                           "remain obligations. Do not print the whole record by default.",
        }
    return result


def repair_focus(project, stage, context):
    history = observation_handoff(project, stage)
    recent = history["recent"]
    transition = history["last_transition"]
    changed_inputs = context.get("input_changes", {}).get("changed", [])
    if not recent and not changed_inputs:
        return None
    return {
        "latest_observation": recent[0] if recent else None,
        "changed_observations": history["changes"],
        "last_transition": ({"from_receipt": transition["previous"]["receipt"],
                             "to_receipt": transition["current"]["receipt"],
                             "changes": transition["changes"]} if transition else None),
        "changed_input_locations": changed_inputs,
        "instruction": "Start with current controller feedback and the cited failure receipt. "
        "These ledger observations are historical, not current worktree measurements. Input "
        "reference changes do not inventory source edits or prove a causal explanation. "
        "Inspect the affected diff and log sections, repair the cause, then rerun affected "
        "checks. Preserve unrelated passing work, but reuse execution only through existing "
        "input-bound receipts. Repeated error text alone does not mean no progress. "
        "A collector change does not establish device behavior; keep unresolved obligations.",
    }


def compact_delivery_brief(context):
    """Do not repeat identical inlined contract excerpts within the same conversation."""
    decisions = context.get('accepted_decisions')
    probes = context.get('recent_early_probes')
    if isinstance(probes, list):
        probes = [dict(value) for value in probes]
        for index, value in enumerate(probes):
            if f'recent_early_probes/{index}/source' in context.get('input_changes', {}).get('unchanged', []):
                value.pop('recipe', None)
                value['previously_supplied'] = True
        context = {**context, 'recent_early_probes': probes}
    if isinstance(decisions, dict):
        decisions = {key: dict(value) for key, value in decisions.items()}
        for key, value in decisions.items():
            if f'accepted_decisions/{key}/source' in context.get('input_changes', {}).get('unchanged', []):
                value.pop('text', None)
                value.pop('recipe', None)
                value['previously_supplied'] = True
        context = {**context, 'accepted_decisions': decisions}
    brief = context.get("delivery_essentials")
    unchanged = context.get("input_changes", {}).get("unchanged", [])
    if not isinstance(brief, dict) or "delivery_essentials/source" not in unchanged:
        return context
    return {**context, "delivery_essentials": {
        **{k: v for k, v in brief.items() if k != "text"},
        "previously_supplied": True,
        "instruction": "The unchanged excerpt was already supplied, not necessarily read or remembered. "
        "Use the bound source section if needed after compaction; all frozen obligations remain required."}}
