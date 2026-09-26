"""Read-only historical prompt replay; no model calls or writes to the source run."""

import argparse
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from driver_port_factory.codex.context_focus import compact_feedback, reading_plan
from driver_port_factory.codex.contracts import CodexArtifact
from driver_port_factory.composition import open_project
from driver_port_factory.core.artifacts import ArtifactStore
from driver_port_factory.core.models import ArtifactDirection


def characters(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True))


def audit(root):
    project = open_project(root, read_only=True)
    rows, skipped = [], []
    with tempfile.TemporaryDirectory(prefix="dpf-focus-audit-") as directory:
        sink = SimpleNamespace(artifacts=ArtifactStore(Path(directory)))
        for ref in project.artifact_refs(direction=ArtifactDirection.INPUT):
            if ref.kind != CodexArtifact.PROMPT.value:
                continue
            raw = project.artifacts.path_for_digest(ref.digest).read_text()
            try:
                header = json.loads(raw.split("<job>", 1)[1].split("</job>", 1)[0])
                context = header["reference_material"]
                stage = project.workflow.parse_stage(header["instructions"]["stage"])
            except (IndexError, KeyError, json.JSONDecodeError):
                skipped.append(ref.digest)
                continue
            result = compact_feedback(sink, context)
            plan = reading_plan(stage, result)
            rows.append({
                "prompt_digest": ref.digest, "stage": stage.value,
                "context_characters_before": characters(context),
                "context_characters_after_feedback_compaction": characters(result),
                "reading_plan_characters": characters(plan) if plan else 0,
                "prioritized_kinds": [r["kind"] for r in plan["first"]] if plan else [],
                "archived_feedback_fields": [k for k in (
                    "controller_execution", "controller_feedback", "checker_findings")
                    if context.get(k) != result.get(k)],
            })
    return {
        "source_project": str(project.root),
        "method": "Read-only historical prompt replay; temporary isolated CAS; no model calls. "
        "Character counts are not tokens or cost savings. Does not simulate future model "
        "behavior or repair ledger changes. Temporary path length can vary between runs.",
        "calls": len(rows), "unparsed_prompt_digests": skipped,
        "feedback_compacted_calls": sum(bool(r["archived_feedback_fields"]) for r in rows),
        "rows": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(audit(arguments.run), ensure_ascii=False, indent=2))
