"""A small worker-facing tool; provenance and publication belong to the controller."""

import json
import re

from .learning import remember_optional


def tool():
    return {
        "name": "knowledge_learn",
        "description": "Optionally save one useful discovery already made during this task. "
        "No extra research/report. Controller archives sources and publishes after acceptance. "
        "Failure never blocks translation; omit when there is nothing worth reusing.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["lesson", "conditions", "sources"],
            "properties": {
                "lesson": {"type": "string", "description": "Short actionable finding."},
                "conditions": {"type": "string", "description": "When it applies and limits."},
                "sources": {
                    "type": "string",
                    "description": "One location per line: target:ostd/src/sync/spin.rs:40-65. "
                    "Use source/target/qemu and repository-relative paths. No hashes.",
                },
            },
        },
    }


def run(project, job_id, arguments):
    from ..migration.route_tool import authorize

    authorize(project, job_id)
    try:
        citations = []
        for line in arguments["sources"].splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r"(source|target|qemu):(.+):(\d+)(?:-(\d+))?", line.strip())
            if not match:
                raise ValueError("Use repository:path:line or repository:path:start-end")
            repo, path, start, end = match.groups()
            citations.append(
                {
                    "repository": repo,
                    "path": path,
                    "line_start": int(start),
                    "line_end": int(end or start),
                }
            )
        result = remember_optional(
            project,
            job_id,
            {
                "lessons": [
                    {
                        "lesson": arguments["lesson"],
                        "conditions": arguments["conditions"],
                        "sources": citations,
                    }
                ]
            },
        )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        result = {"status": "NOT_RECORDED", "reason": str(error)}
    return json.dumps({**result, "instruction": "Continue the current task; learning is optional."})
