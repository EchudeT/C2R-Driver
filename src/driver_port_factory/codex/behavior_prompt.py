"""A behavior round receives execution instructions, not another workflow entrypoint."""


def active(stage, context):
    progress = (context or {}).get("behavior_progress")
    return (
        stage.value == "driver_implementation"
        and not (context or {}).get("checker_decision")
        and bool(progress)
        and bool(progress.get("current") or not progress.get("plan_exists"))
    )


def documents(stage, context, supplied):
    if not active(stage, context):
        return supplied
    # Detailed translation/safety/test references remain available. The controller
    # already supplied scope, analysis and scheduling; do not advertise another router.
    routers = {
        "knowledge-guided-driver-port/SKILL.md",
        "knowledge-guided-driver-port/references/workflow.md",
    }
    return tuple(d for d in supplied if d.relative_path not in routers)


def template(stage, context, pack):
    focused = {
        "environment_recovery": "environment-job.md",
        "target_platform_study": "analysis-job.md",
        "migration_contracts": "analysis-job.md",
    }.get(stage.value)
    if focused and (pack.root / focused).is_file():
        return (pack.root / focused).read_text()
    path = pack.root / "behavior-job.md"
    return path.read_text() if active(stage, context) and path.is_file() else pack.template


def focused_context(stage, context):
    """Render-only projection; never changes plan keys, obligations or progress."""
    from copy import deepcopy

    if not active(stage, context) or not context["behavior_progress"].get("current"):
        return dict(context or {})
    value = deepcopy(context)
    progress = value["behavior_progress"]
    passages = {}
    by_text = {}

    def intern(node):
        if isinstance(node, list):
            return [intern(item) for item in node]
        if not isinstance(node, dict):
            return node
        result = {}
        for key, item in node.items():
            if key in {"outcome", "text", "question_text"} and isinstance(item, str):
                if item not in by_text:
                    ref = f"D{len(passages) + 1}"
                    by_text[item] = ref
                    passages[ref] = item
                result[key] = {"text_ref": by_text[item]}
            else:
                result[key] = intern(item)
        return result

    for key in ("current", "route_context"):
        if key in progress:
            progress[key] = intern(progress[key])
    progress["passages"] = passages
    progress["passage_usage"] = (
        "text_ref resolves to passages below; read each distinct passage once. "
        "Only current is scheduled. Shared route/contracts do not add other objectives. "
        "Full analysis stays available through analysis_document."
    )
    # The selected route/obligations are already supplied; keep general report navigation.
    brief = value.get("delivery_essentials")
    if isinstance(brief, dict) and brief.get("source"):
        value["delivery_essentials"] = {
            "source": brief["source"],
            "instruction": "Whole-delivery navigation on demand; current behavior is below.",
        }
    return value
