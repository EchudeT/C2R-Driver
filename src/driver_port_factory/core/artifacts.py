from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .models import ArtifactRef, WorkflowError


class ArtifactStore:
    """Immutable SHA256 content-addressed storage."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.objects = self.root / "objects" / "sha256"
        self.objects.mkdir(parents=True, exist_ok=True)

    def _path_for_digest(self, digest: str) -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise WorkflowError(f"invalid SHA256 digest: {digest}")
        return self.objects / digest[:2] / digest[2:]

    def put_bytes(self, data: bytes, *, kind: str, source: str | None = None) -> ArtifactRef:
        digest = hashlib.sha256(data).hexdigest()
        target = self._path_for_digest(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != data:
                raise WorkflowError(f"CAS collision or corruption for {digest}")
        else:
            temporary = target.with_suffix(f".tmp-{os.getpid()}")
            temporary.write_bytes(data)
            os.replace(temporary, target)
        return ArtifactRef(
            digest=digest,
            kind=kind,
            size=len(data),
            cas_path=str(target.relative_to(self.root)),
            source=source,
        )

    def put_file(self, path: Path, *, kind: str) -> ArtifactRef:
        path = path.resolve()
        if not path.is_file():
            raise WorkflowError(f"artifact is not a regular file: {path}")
        return self.put_bytes(path.read_bytes(), kind=kind, source=str(path))

    def read(self, ref: ArtifactRef) -> bytes:
        path = self.root / ref.cas_path
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != ref.digest:
            raise WorkflowError(f"artifact failed integrity verification: {ref.digest}")
        return data

    def verify(self, ref: ArtifactRef) -> bool:
        try:
            self.read(ref)
        except (OSError, WorkflowError):
            return False
        return True
