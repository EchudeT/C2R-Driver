"""Local append-only evidence library; snapshots are immutable, queries never write."""

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ..core.ledger import canonical_json
from ..core.models import WorkflowError, utc_now

DIGEST = re.compile(r"[0-9a-f]{64}")


def encoded(value):
    return (canonical_json(value) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require_digest(value):
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise WorkflowError("Invalid shared knowledge content identity")
    return value


class Library:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    @contextmanager
    def writing(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "writer.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def write(self, relative, data):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def put(self, data):
        key = digest(data)
        path = self.root / "objects" / key
        if path.exists():
            if path.read_bytes() != data:
                raise WorkflowError("Shared knowledge object is corrupt")
        else:
            self.write(Path("objects") / key, data)
        return key

    def read(self, key):
        require_digest(key)
        try:
            data = (self.root / "objects" / key).read_bytes()
        except OSError as error:
            raise WorkflowError(f"Shared knowledge object unavailable: {key}") from error
        if digest(data) != key:
            raise WorkflowError(f"Shared knowledge object changed: {key}")
        return data

    def head(self):
        try:
            return require_digest((self.root / "HEAD").read_text().strip())
        except OSError as error:
            raise WorkflowError("Shared library has no published snapshot") from error

    def snapshot(self, key=None):
        key = key or self.head()
        value = json.loads(self.read(key))
        if value.get("schema_version") != 1 or value.get("type") != "library_snapshot":
            raise WorkflowError("Invalid shared library snapshot")
        if not isinstance(value.get("entries"), dict):
            raise WorkflowError("Invalid shared library entry map")
        return key, value

    def entry(self, key, snapshot=None):
        _, manifest = self.snapshot(snapshot)
        if key not in manifest["entries"]:
            raise WorkflowError("Entry is not in selected library snapshot")
        return json.loads(self.read(key)), manifest["entries"][key]

    def commit(self, entries=(), *, retire=None):
        """Caller holds writing lock. Entries are idempotent; retirement never erases history."""
        if (self.root / "HEAD").exists():
            parent, previous = self.snapshot()
            states = dict(previous["entries"])
        else:
            parent, states = None, {}
        added = []
        for entry in entries:
            key = self.put(encoded(entry))
            added.append(key)
            states.setdefault(key, {"status": "ACTIVE", "acquired_at": utc_now()})
        if retire:
            key, reason = retire
            if key not in states or not isinstance(reason, str) or not reason.strip():
                raise WorkflowError("Retirement needs an existing entry and a specific reason")
            states[key] = {
                **states[key],
                "status": "RETIRED",
                "reason": reason,
                "retired_at": utc_now(),
            }
        if parent and states == previous["entries"]:
            return {"snapshot": parent, "entries": added}
        snapshot = self.put(
            encoded(
                {
                    "schema_version": 1,
                    "type": "library_snapshot",
                    "parent": parent,
                    "entries": states,
                }
            )
        )
        self.write("HEAD", (snapshot + "\n").encode())
        return {"snapshot": snapshot, "entries": added}
