"""Bounded symbol navigation without parsing the frozen AST on every query."""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any

from ..core.models import WorkflowError
from ..core.project import Project
from .navigation import details, open_navigation


def query_facts(
    project: Project, *, symbol: str, source_path: str | None = None, limit: int = 5
) -> dict[str, Any]:
    return query_symbols(project, symbols=[symbol], source_path=source_path, limit=limit)[0]


def query_symbols(
    project: Project, *, symbols: list[str], source_path: str | None = None, limit: int = 5,
    detail: str = "summary",
) -> list[dict[str, Any]]:
    if (
        not symbols
        or len(symbols) > 32
        or not all(isinstance(symbol, str) and symbol for symbol in symbols)
        or not 1 <= limit <= 20
        or detail not in {"summary", "calls", "cfg"}
    ):
        raise WorkflowError("C facts requires 1–32 symbols and limit between 1 and 20")
    queries = []
    with closing(open_navigation(project)) as db:
        for symbol in dict.fromkeys(symbols):
            results = []
            count = 0
            for number, category, payload in db.execute(
                "SELECT unit, category, payload FROM definitions "
                "WHERE name = ? ORDER BY unit, rowid",
                (symbol,),
            ):
                identity = json.loads(payload)
                if source_path and not any(
                    item.get("path", "").startswith(source_path)
                    for item in identity.get("closure_source", [])
                ):
                    continue
                count += 1
                if len(results) < limit:
                    results.append(details(db, project, number, category, identity, detail=detail))
            queries.append(
                {
                    "symbol": symbol,
                    "match_count": count,
                    "truncated": count > len(results),
                    "results": results,
                    "scope": (
                        "Structured navigation only; "
                        "inspect cited originals for behavior and safety."
                    ),
                }
            )
    return queries
