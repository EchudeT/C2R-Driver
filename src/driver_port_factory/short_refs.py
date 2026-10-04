"""Task-local append-only references; complete identities remain in the controller store."""

import hashlib
import json
import os
import re
import secrets
import sqlite3
from pathlib import Path

from .core.models import WorkflowError

TOKEN = re.compile(r"R[0-9a-f]{8}-[1-9][0-9]*")
HASH = re.compile(r"[0-9a-f]{40,64}")


class References:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / ".dpf" / "references.sqlite3"
        config = self.root / ".dpf/project.json"
        self.compact_paths = (
            json.loads(config.read_text()).get("compact_paths", False)
            if config.is_file()
            else False
        )

    def file_path(self, value):
        """Expose a real short filesystem entry for immutable CAS evidence only."""
        if not self.compact_paths or not isinstance(value, str):
            return value
        candidate = Path(value)
        cas = self.root / ".dpf/cas/objects/sha256"
        if not candidate.is_absolute() or not candidate.is_relative_to(cas):
            return value
        relative = candidate.relative_to(cas)
        if (
            len(relative.parts) != 2
            or len(relative.parts[0]) != 2
            or len(relative.parts[1]) != 62
            or not HASH.fullmatch("".join(relative.parts))
            or not candidate.is_file()
        ):
            return value
        token = self.put("file_path", {"path": value, "sha256": "".join(relative.parts)})
        directory = self.root / ".dpf/e"
        directory.mkdir(exist_ok=True)
        alias = directory / ("E" + token.split("-")[1])
        try:
            alias.symlink_to(os.path.relpath(candidate, directory))
        except FileExistsError:
            pass
        if not alias.is_symlink() or alias.resolve() != candidate.resolve():
            raise WorkflowError("Evidence path alias changed; refusing a different target")
        return str(alias)

    def connect(self):
        if not (self.root / ".dpf").is_dir():
            raise WorkflowError("Short references require a controller workspace")
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("CREATE TABLE IF NOT EXISTS scope (name TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS refs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "kind TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL UNIQUE)"
        )
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT name FROM scope").fetchone():
            db.execute("INSERT INTO scope VALUES (?)", (secrets.token_hex(4),))
        return db

    def put(self, kind, value):
        payload = json.dumps({"kind": kind, "value": value}, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        db = self.connect()
        try:
            db.execute(
                "INSERT OR IGNORE INTO refs(kind,payload,digest) VALUES (?,?,?)",
                (kind, payload, digest),
            )
            number = db.execute("SELECT id FROM refs WHERE digest=?", (digest,)).fetchone()[0]
            scope = db.execute("SELECT name FROM scope").fetchone()[0]
            db.commit()
            return f"R{scope}-{number}"
        finally:
            db.close()

    def get(self, token, kind=None):
        if not isinstance(token, str) or not TOKEN.fullmatch(token) or not self.path.exists():
            raise WorkflowError("Unknown short reference; query evidence again")
        with sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True) as db:
            scope = db.execute("SELECT name FROM scope").fetchone()[0]
            if token.split("-")[0] != "R" + scope:
                raise WorkflowError("Reference belongs to a different workspace")
            row = db.execute(
                "SELECT kind,payload,digest FROM refs WHERE id=?", (int(token.split("-")[1]),)
            ).fetchone()
        if row is None or hashlib.sha256(row[1].encode()).hexdigest() != row[2]:
            raise WorkflowError("Reference is missing or corrupt")
        value = json.loads(row[1])
        if value["kind"] != row[0] or (kind and row[0] != kind):
            raise WorkflowError("Reference has the wrong type")
        return value["value"]


def active():
    root = os.environ.get("DPF_WORKER_PROJECT")
    return References(root) if root else None


def compact(value, refs):
    """Presentation only. Keep the entire unmodified object addressable for audit."""
    if isinstance(value, list):
        return [compact(item, refs) for item in value]
    if not isinstance(value, dict):
        return refs.file_path(value)
    result = {}
    hidden = False
    for key, item in value.items():
        if (
            "sha256" in key
            or key
            in {"digest", "blob", "snapshot", "revision", "entry", "entry_id", "packet_sha256"}
        ) and (isinstance(item, str) and HASH.fullmatch(item)):
            hidden = True
            continue
        if key == "cas_path" or (
            key == "chunk_id" and isinstance(item, str) and HASH.fullmatch(item.split(":", 1)[0])
        ):
            hidden = True
            continue
        result[key] = compact(item, refs)
    if hidden:
        result["evidence_ref"] = refs.put("provenance", value)
    return result


def emit(value, *, full=False):
    refs = active()
    return value if full or refs is None else compact(value, refs)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Inspect controller-bound short references")
    parser.add_argument("reference")
    parser.add_argument("--project", type=Path)
    args = parser.parse_args()
    refs = References(args.project) if args.project else active()
    if refs is None:
        parser.error("No worker project; supply --project")
    print(json.dumps(refs.get(args.reference), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
