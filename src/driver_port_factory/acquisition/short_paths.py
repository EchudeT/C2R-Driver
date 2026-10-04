"""Append-only physical directory allocation; identities stay in acquisition records."""

import json
import sqlite3
from pathlib import Path

from ..core.models import WorkflowError


def allocate(project_root, control_root, role, identity, *, writable=False):
    prefix = "work" if writable else ".dpf/worktrees"
    group = f"{prefix}/{role}"
    payload = json.dumps(identity, sort_keys=True)
    with sqlite3.connect(Path(control_root) / "paths.sqlite3", timeout=10) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS paths (family TEXT, identity TEXT, path TEXT UNIQUE, "
            "PRIMARY KEY(family, identity))"
        )
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT path FROM paths WHERE family=? AND identity=?", (group, payload)
        ).fetchone()
        if row:
            path = Path(project_root) / row[0]
            if path.parent != Path(project_root) / prefix or not path.name.startswith(role):
                raise WorkflowError("Invalid short-path allocation")
            return path
        number = 1
        while True:
            relative = group if number == 1 else f"{group}-{number}"
            path = Path(project_root) / relative
            taken = db.execute("SELECT 1 FROM paths WHERE path=?", (relative,)).fetchone()
            if not taken and not path.exists() and not path.is_symlink():
                break
            number += 1
        db.execute("INSERT INTO paths VALUES (?,?,?)", (group, payload, relative))
        return path
