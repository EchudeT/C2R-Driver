"""Index unchanged execution manuals in the same session; keep obligations inline."""

import json
from copy import deepcopy

from .behavior_prompt import active

MATERIALS = (
    ("tool_runtime", "platform_execution"),
    ("prepared_public_tests",),
    ("environment_evidence",),
)


def compact_material(project, stage, context, known):
    """Return prompt material and identities saved only after a successful invocation.

    The caller clears known identities for a new session or observed compaction.
    This affects navigation only, never scheduling, test selection or acceptance.
    """
    if not active(stage, context):
        return context, {}
    value = deepcopy(context)
    identities = {}
    for location in MATERIALS:
        parent = value
        for key in location[:-1]:
            parent = parent.get(key, {})
        material = parent.get(location[-1])
        if not isinstance(material, dict):
            continue
        raw = json.dumps(material, ensure_ascii=False, sort_keys=True).encode()
        if len(raw) < 1000:
            continue  # An index would add overhead to small entries.
        artifact = project.artifacts.put_bytes(raw, kind="execution_reference")
        identity = "execution_reference/" + "/".join(location)
        identities[identity] = artifact.digest
        if known.get(identity) != artifact.digest:
            continue
        parent[location[-1]] = {
            **{k: material[k] for k in ("interface", "cases") if k in material},
            "source": {
                "kind": "execution_reference",
                "digest": artifact.digest,
                "path": str(project.artifacts.path_for_digest(artifact.digest)),
            },
            "instruction": (
                "Unchanged execution reference supplied earlier in this session. "
                "Read the indexed JSON on demand if needed; do not assume it was remembered. "
                "Case IDs describe final acceptance; current development checks follow the "
                "selected B/C passages. Current obligations and feedback remain inline."
            ),
        }
    return value, identities
