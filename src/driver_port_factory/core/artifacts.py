from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .models import ArtifactContent, ArtifactRef, WorkflowError


class ArtifactStore:
    """Immutable SHA256 content-addressed storage."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.objects = self.root / "objects" / "sha256"
        self.objects.mkdir(parents=True, exist_ok=True)

    def path_for_digest(self, digest: str) -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise WorkflowError(f"invalid SHA256 digest: {digest}")
        return self.objects / digest[:2] / digest[2:]

    def put_bytes(self, data: bytes, *, kind: str) -> ArtifactContent:
        digest = hashlib.sha256(data).hexdigest()
        target = self.path_for_digest(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != data:
                raise WorkflowError(f"CAS collision or corruption for {digest}")
        else:
            temporary = target.with_suffix(f".tmp-{os.getpid()}")
            temporary.write_bytes(data)
            os.replace(temporary, target)
        return ArtifactContent(
            digest=digest,
            kind=kind,
            size=len(data),
            cas_path=target.relative_to(self.root).as_posix(),
        )

    def read(self, ref: ArtifactContent | ArtifactRef) -> bytes:
        path = self.path_for_digest(ref.digest)
        canonical = path.relative_to(self.root).as_posix()
        if ref.cas_path != canonical:
            raise WorkflowError(f"artifact has non-canonical CAS path: {ref.digest}")
        data = path.read_bytes()
        if len(data) != ref.size:
            raise WorkflowError(f"artifact size metadata is invalid: {ref.digest}")
        if hashlib.sha256(data).hexdigest() != ref.digest:
            raise WorkflowError(f"artifact failed integrity verification: {ref.digest}")
        return data

    def verify(self, ref: ArtifactContent | ArtifactRef) -> bool:
        try:
            self.read(ref)
        except (OSError, WorkflowError):
            return False
        return True
