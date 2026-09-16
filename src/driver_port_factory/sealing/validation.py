from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import stat
import tarfile
from pathlib import Path
from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import (
    ArtifactValidator,
    BundleValidationContext,
    BundleValidator,
    json_object,
    json_object_document,
    nonempty,
)
from ..evaluation.contracts import EvaluationArtifact
from .contracts import SealingArtifact, SealingEvent, SealingStage, TimestampReceiptKind

VALIDATORS = MappingProxyType[SealingArtifact, ArtifactValidator](
    {
        SealingArtifact.CANDIDATE_MANIFEST: json_object_document,
        SealingArtifact.CANDIDATE_BUNDLE: nonempty,
        SealingArtifact.CANDIDATE_LEDGER_EVENT: json_object_document,
        SealingArtifact.CANDIDATE_TIMESTAMP_RECEIPT: json_object_document,
        SealingArtifact.CANDIDATE_DIGEST_ANCHOR: json_object_document,
        SealingArtifact.CANDIDATE_TRANSFER_RECORD: json_object_document,
    }
)

BUNDLE_VALIDATORS = MappingProxyType[SealingStage, BundleValidator](
    {SealingStage.CANDIDATE_SEALING: lambda context: _CandidateGate(context).validate()}
)


class _CandidateGate:
    def __init__(self, context: BundleValidationContext) -> None:
        self.context = context
        self.manifest = json_object(
            context.one_current(SealingArtifact.CANDIDATE_MANIFEST)[1], "candidate manifest"
        )
        self.bundle = context.one_current(SealingArtifact.CANDIDATE_BUNDLE)[1]
        self.ledger = json_object(
            context.one_current(SealingArtifact.CANDIDATE_LEDGER_EVENT)[1],
            "candidate ledger event",
        )
        self.receipt = json_object(
            context.one_current(SealingArtifact.CANDIDATE_TIMESTAMP_RECEIPT)[1],
            "candidate timestamp receipt",
        )
        self.transfer = json_object(
            context.one_current(SealingArtifact.CANDIDATE_TRANSFER_RECORD)[1],
            "candidate transfer record",
        )

    def validate(self) -> None:
        digest = hashlib.sha256(self.bundle).hexdigest()
        try:
            TimestampReceiptKind(self.receipt["kind"])
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("candidate timestamp receipt is not externally typed") from error
        if (
            self.manifest.get("schema_version") != 2
            or self.manifest.get("candidate_sha256") != digest
            or self.manifest.get("bundle_size") != len(self.bundle)
            or self.receipt.get("candidate_sha256") != digest
            or self.transfer.get("candidate_sha256") != digest
            or self.ledger.get("candidate_sha256") != digest
            or self.manifest.get("private_evaluation") != "NOT_RUN_BY_MIGRATOR"
            or self.transfer.get("private_evaluation") != "NOT_RUN_BY_MIGRATOR"
            or self.manifest.get("request", {}).get("private_material_access") is not False
            or self.manifest.get("request", {}).get("no_cross_task_update") is not True
        ):
            raise WorkflowError("candidate seal identities or blind boundary are inconsistent")
        members, internal = self._archive()
        records = internal.get("entities")
        if not isinstance(records, list):
            raise WorkflowError("candidate bundle has no entity ledger")
        expected = {
            item["path"]: (item["size"], item["sha256"])
            for item in records
            if isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and isinstance(item.get("size"), int)
            and isinstance(item.get("sha256"), str)
        }
        actual = {
            name: (len(data), hashlib.sha256(data).hexdigest())
            for name, data in members.items()
            if name != "manifest.json"
        }
        required = (
            "candidate-source/",
            "runtime/",
            "public-runs/",
            "rules/",
            "artifacts/",
            "pmc/capability-map.json",
            "provenance/ledger-before-seal.json",
            "target/changes.patch",
        )
        if expected != actual or any(
            not any(path == prefix or path.startswith(prefix) for path in actual)
            for prefix in required
        ):
            raise WorkflowError("candidate bundle is reference-only or an entity hash changed")
        if any(EvaluationArtifact.PRIVATE_ASSERTIONS.value in path for path in actual):
            raise WorkflowError("NON_INDEPENDENT")
        self._public_attempts(actual)
        self._ledger_event()
        self._transfer(digest)

    def _archive(self) -> tuple[dict[str, bytes], dict]:
        members = {}
        try:
            with tarfile.open(fileobj=io.BytesIO(self.bundle), mode="r:") as archive:
                names = archive.getnames()
                if names != sorted(names) or len(names) != len(set(names)):
                    raise WorkflowError("candidate archive order is not deterministic")
                for member in archive.getmembers():
                    if (
                        not member.isfile()
                        or member.mtime != 0
                        or member.uid != 0
                        or member.gid != 0
                        or stat.S_IMODE(member.mode) != 0o444
                    ):
                        raise WorkflowError("candidate archive metadata is not frozen")
                    opened = archive.extractfile(member)
                    if opened is None:
                        raise WorkflowError("candidate archive entity is unreadable")
                    members[member.name] = opened.read()
            internal = json.loads(members["manifest.json"])
        except (KeyError, tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("candidate archive is malformed") from error
        if hashlib.sha256(members["manifest.json"]).hexdigest() != self.manifest.get(
            "entity_manifest_sha256"
        ):
            raise WorkflowError("candidate entity manifest differs from the seal")
        return members, internal

    def _public_attempts(self, actual: dict[str, tuple[int, str]]) -> None:
        connection = sqlite3.connect(self.context.project_root / ".dpf" / "run.sqlite3")
        try:
            rows = connection.execute(
                "SELECT digest, kind FROM stage_artifacts WHERE kind IN (?, ?)",
                ("public_qemu_attempt", "public_repair_attempt"),
            ).fetchall()
        finally:
            connection.close()
        for digest, kind in rows:
            if not any(kind in path and digest in path for path in actual):
                raise WorkflowError("candidate omitted a preserved public failure record")

    def _ledger_event(self) -> None:
        connection = sqlite3.connect(self.context.project_root / ".dpf" / "run.sqlite3")
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT * FROM events WHERE event_hash = ?", (self.ledger.get("event_hash"),)
            ).fetchone()
        finally:
            connection.close()
        if (
            row is None
            or row["event_type"] != SealingEvent.CANDIDATE_SEALED.value
            or row["previous_hash"] != self.ledger.get("previous_digest")
            or json.loads(row["payload"])
            != {key: value for key, value in self.ledger.items() if key != "event_hash"}
        ):
            raise WorkflowError("candidate seal ledger event is absent or detached")

    def _transfer(self, digest: str) -> None:
        root = Path(str(self.transfer.get("destination", ""))).resolve()
        expected = {
            "candidate.tar": self.bundle,
            "candidate-manifest.json": _json(self.manifest),
            "candidate-ledger-event.json": _json(self.ledger),
            "timestamp-receipt.json": _json(self.receipt),
            "transfer-record.json": _json(self.transfer),
        }
        if (
            not root.is_dir()
            or stat.S_IMODE(root.stat().st_mode) != 0o555
            or any(
                not (root / name).is_file()
                or (root / name).read_bytes() != data
                or stat.S_IMODE((root / name).stat().st_mode) != 0o444
                for name, data in expected.items()
            )
            or hashlib.sha256((root / "candidate.tar").read_bytes()).hexdigest() != digest
        ):
            raise WorkflowError("candidate evaluator transfer is not immutable or digest-bound")


def _json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
