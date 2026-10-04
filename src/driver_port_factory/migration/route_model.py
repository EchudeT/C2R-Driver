"""Pure reference model for coarse paths, frozen obligations and behavior dependencies."""

import hashlib
import json
import re
from copy import deepcopy

from ..core.models import WorkflowError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def section(text, title):
    if not isinstance(title, str) or not title:
        raise WorkflowError("Route references require a Markdown section title")
    lines = text.splitlines()
    headings = [
        (i, len(m[1]), m[2])
        for i, line in enumerate(lines)
        if (m := re.fullmatch(r"(#{1,6}) (.+)", line))
    ]
    found = [(i, depth) for i, depth, name in headings if name == title]
    if len(found) != 1:
        raise WorkflowError(f"Route section must identify one exact heading: {title}")
    start, depth = found[0]
    end = next((i for i, level, _ in headings if i > start and level <= depth), len(lines))
    body = "\n".join(lines[start + 1 : end]).strip()
    if not body:
        raise WorkflowError(f"Route section has no content: {title}")
    return body


def strings(value, name, *, nonempty=False):
    if (
        not isinstance(value, list)
        or (nonempty and not value)
        or any(not isinstance(v, str) or not v for v in value)
        or len(set(value)) != len(value)
    ):
        raise WorkflowError(f"{name} must be unique string references")
    return value


def normalize(index):
    """Expand documented defaults, without guessing references or interpreting prose."""
    value = deepcopy(index)
    if not isinstance(value, dict):
        return value
    for name in ("premises", "learn"):
        value.setdefault(name, [])
    for name in ("main_route", "behaviors", "contracts", "premises"):
        if not isinstance(value.get(name), list):
            continue
        for row in value[name]:
            if not isinstance(row, dict):
                continue
            row.setdefault("section", row.get("id"))
            if name == "behaviors":
                row.setdefault("depends_on", [])
            if name == "premises":
                row.setdefault("sources", [])
                row.setdefault("probe_receipts", [])
                row.setdefault("question_section", row["section"])
    return value


class Checks:
    def __init__(self):
        self.errors = []

    def check(self, location, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except WorkflowError as error:
            self.errors.append(f"{location}: {error}")
            return None

    def require(self, condition, message):
        if not condition:
            self.errors.append(message)

    def finish(self):
        if self.errors:
            raise WorkflowError(
                "Analysis needs corrections (fix together in this turn):\n- "
                + "\n- ".join(dict.fromkeys(self.errors))
            )


def _rows(value, name, fields, checks, *, nonempty=False):
    rows = value.get(name)
    if not isinstance(rows, list) or len(rows) > 128 or (nonempty and not rows):
        checks.errors.append(f"Invalid coarse route collection: {name}")
        return {}
    result = {}
    for n, row in enumerate(rows):
        location = f"{name}[{n}]"
        expected = fields | {"id", "section"}
        if not isinstance(row, dict):
            checks.errors.append(f"{location}: expected an object")
            continue
        if set(row) != expected:
            checks.errors.append(
                f"{location}: missing {sorted(expected - row.keys())}; "
                f"unknown {sorted(row.keys() - expected)}"
            )
            continue
        name_id = row["id"]
        if not isinstance(name_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name_id):
            checks.errors.append(f"{location}: Route IDs must be short names")
            continue
        if name_id in result:
            checks.errors.append(f"{location}: Duplicate route reference: {name_id}")
            continue
        result[name_id] = row
    return result


def _citations(values):
    if not isinstance(values, list):
        raise WorkflowError("Source citations must be a list")
    for row in values:
        if not isinstance(row, dict) or set(row) != {
            "repository",
            "path",
            "line_start",
            "line_end",
        }:
            raise WorkflowError("Citations require repository/path/line_start/line_end")
        if (
            row["repository"] not in ("source", "target", "qemu")
            or not isinstance(row["path"], str)
            or not row["path"]
            or type(row["line_start"]) is not int
            or type(row["line_end"]) is not int
            or not 1 <= row["line_start"] <= row["line_end"]
        ):
            raise WorkflowError("Invalid source citation")


def validate(index, text, *, ready=False):
    index = normalize(index)
    checks = Checks()
    if not isinstance(index, dict) or set(index) != {
        "main_route",
        "behaviors",
        "contracts",
        "premises",
        "learn",
    }:
        raise WorkflowError(
            "Route index requires main_route, behaviors, contracts; "
            "optional premises and learn. No other top-level fields."
        )
    routes = _rows(index, "main_route", set(), checks, nonempty=True)
    contracts = _rows(index, "contracts", {"sources"}, checks, nonempty=ready)
    behaviors = _rows(
        index, "behaviors", {"route", "contracts", "depends_on"}, checks, nonempty=True
    )
    premises = _rows(
        index,
        "premises",
        {"question_section", "route", "status", "sources", "probe_receipts"},
        checks,
    )
    for group in (routes, contracts, behaviors, premises):
        for row in group.values():
            checks.check(f"{row['id']}.section", section, text, row["section"])
    for row in contracts.values():
        valid = _source_checks(row, checks)
        checks.require(
            valid and any(c["repository"] == "source" for c in row["sources"]),
            f"{row['id']}: A contract requires its source obligation location",
        )
    linked = set()
    for row in behaviors.values():
        for field, known in (
            ("route", routes),
            ("contracts", contracts),
            ("depends_on", behaviors),
        ):
            refs = _references(
                row,
                field,
                known,
                checks,
                nonempty=field == "route" or (ready and field == "contracts"),
            )
            if field == "contracts":
                linked.update(refs)
    checks.require(
        set(contracts) == linked,
        "Every declared source contract must have an implementing behavior",
    )
    _premises(premises, routes, text, checks, ready=ready)
    refs = checks.check("learn", strings, index["learn"], "learn")
    checks.require(
        refs is None or set(refs) <= premises.keys(),
        "Knowledge selections must reference existing premises",
    )
    checks.finish()
    return index


def _references(row, field, known, checks, *, nonempty=False):
    refs = checks.check(f"{row['id']}.{field}", strings, row[field], field, nonempty=nonempty)
    if refs is None:
        return []
    checks.require(set(refs) <= known.keys(), f"{row['id']}: Unknown {field} reference")
    return refs


def _source_checks(row, checks):
    before = len(checks.errors)
    values = row["sources"]
    if not isinstance(values, list):
        checks.errors.append(f"{row['id']}.sources must be a list")
        return False
    for n, citation in enumerate(values):
        checks.check(f"{row['id']}.sources[{n}]", _citations, [citation])
    return len(checks.errors) == before


def _premises(premises, routes, text, checks, *, ready):
    for row in premises.values():
        name = row["id"]
        _references(row, "route", routes, checks, nonempty=True)
        _source_checks(row, checks)
        checks.check(f"{name}.question_section", section, text, row["question_section"])
        receipts = checks.check(
            f"{name}.probe_receipts", strings, row["probe_receipts"], "controller probe receipt IDs"
        )
        if receipts is not None:
            checks.require(
                all(re.fullmatch(r"P[1-9][0-9]*", r) for r in receipts),
                f"{name}.probe_receipts: use only IDs returned by analysis probe "
                "(e.g. P1); source explanations belong in Markdown, omit this field "
                "when no probe ran",
            )
        checks.require(
            row["status"] in ("open", "supported"),
            f"{name}: Premise status must be open or supported",
        )
        checks.require(
            row["status"] != "supported" or row["sources"] or row["probe_receipts"],
            f"{name}: A supported premise requires source or observed evidence",
        )
        checks.require(
            not (ready and row["status"] == "open"),
            f"{name}: Resolve or report blocked for the design-changing premise",
        )


def obligations(index, text):
    return {c["id"]: section(text, c["section"]) for c in index["contracts"]}


def rows(index, text):
    return [
        {
            "id": b["id"],
            "outcome": section(text, b["section"]),
            "contracts": b["contracts"],
            "depends_on": b["depends_on"],
            "constraints": [],
        }
        for b in index["behaviors"]
    ]


def packet(index, text, behavior_ids):
    selected = [b for b in index["behaviors"] if b["id"] in behavior_ids]
    route_ids = {r for b in selected for r in b["route"]}
    contract_ids = {c for b in selected for c in b["contracts"]}
    return {
        "main_route": [
            {"id": r["id"], "text": section(text, r["section"])}
            for r in index["main_route"]
            if r["id"] in route_ids
        ],
        "contracts": [
            c | {"text": section(text, c["section"])}
            for c in index["contracts"]
            if c["id"] in contract_ids
        ],
        "premises": [
            p
            | {
                "text": section(text, p["section"]),
                "question_text": section(text, p["question_section"]),
            }
            for p in index["premises"]
            if route_ids.intersection(p["route"])
        ],
    }


def bases(index, text):
    # Observation additions do not change the chosen design. Final acceptance still binds evidence.
    result = {}
    for b in index["behaviors"]:
        value = packet(index, text, {b["id"]})
        value["premises"] = [
            {k: p[k] for k in ("id", "text", "question_text", "status", "route")}
            for p in value["premises"]
        ]
        value["contracts"] = [{k: c[k] for k in ("id", "text")} for c in value["contracts"]]
        result[b["id"]] = digest(value)
    return result
