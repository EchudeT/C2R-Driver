from __future__ import annotations

from pathlib import Path

import pytest

from driver_port_factory.acquisition.repository_checkout import CheckoutRecord
from driver_port_factory.acquisition.repository_role import RepositoryRole
from driver_port_factory.acquisition.source_identity import (
    SourceIdentityRecord,
    SourceIdentityVerifier,
    validate_source_identity,
)
from driver_port_factory.core.models import WorkflowError
from tests.acquisition_support import git, repository


def _checkout(root: Path, source: Path) -> CheckoutRecord:
    commit = git("rev-parse", "HEAD", cwd=source)
    tree = git("rev-parse", "HEAD^{tree}", cwd=source)
    return CheckoutRecord(
        RepositoryRole.SOURCE,
        "example-source",
        str(source.resolve()),
        commit,
        commit,
        tree,
        str(source.resolve()),
        str(source.relative_to(root)),
        True,
        "locks/source.json",
        "a" * 64,
        "2026-01-01T00:00:00+00:00",
    )


def test_directory_source_entry_is_verified_as_a_git_tree(tmp_path: Path) -> None:
    source = repository(
        tmp_path,
        "source",
        {
            "drivers/e1000/Makefile": "obj-m += e1000.o\n",
            "drivers/e1000/e1000_main.c": "int e1000_probe(void) { return 0; }\n",
            "drivers/e1000/e1000_hw.h": "#define E1000_REG 0x00\n",
        },
    )
    checkout = _checkout(tmp_path, source)
    envelope = {"source_driver_entry_or_repository_hint": "drivers/e1000"}

    identity = SourceIdentityVerifier().verify(
        project_root=tmp_path,
        migration_envelope_sha256="b" * 64,
        migration_envelope=envelope,
        source=checkout,
    )

    assert identity.observed_identity.object_kind == "tree"
    assert git(
        "cat-file",
        "-t",
        f"{checkout.resolved_commit}:drivers/e1000",
        cwd=source,
    ) == "tree"
    assert any("recursive Git tree listing" in item for item in identity.observations)

    # The persisted form is accepted by the artifact parser and re-verifies
    # against the same frozen checkout.
    parsed = SourceIdentityRecord.from_dict(identity.to_dict())
    validate_source_identity(
        parsed,
        project_root=tmp_path,
        migration_envelope_sha256="b" * 64,
        migration_envelope=envelope,
        source=checkout,
    )


def test_directory_source_entry_rejects_worktree_drift(tmp_path: Path) -> None:
    source = repository(
        tmp_path,
        "source",
        {"drivers/e1000/e1000_main.c": "int e1000_probe(void) { return 0; }\n"},
    )
    checkout = _checkout(tmp_path, source)
    envelope = {"source_driver_entry_or_repository_hint": "drivers/e1000"}
    (source / "drivers/e1000/e1000_main.c").write_text(
        "int e1000_probe(void) { return 1; }\n", encoding="utf-8"
    )

    with pytest.raises(WorkflowError, match="directory worktree differs|directory files differ"):
        SourceIdentityVerifier().verify(
            project_root=tmp_path,
            migration_envelope_sha256="b" * 64,
            migration_envelope=envelope,
            source=checkout,
        )


def test_legacy_file_identity_without_object_kind_remains_compatible(tmp_path: Path) -> None:
    source = repository(tmp_path, "source", {"drivers/e1000_main.c": "int main(void) {}\n"})
    checkout = _checkout(tmp_path, source)
    envelope = {"source_driver_entry_or_repository_hint": "drivers/e1000_main.c"}
    identity = SourceIdentityVerifier().verify(
        project_root=tmp_path,
        migration_envelope_sha256="b" * 64,
        migration_envelope=envelope,
        source=checkout,
    )
    legacy = identity.to_dict()
    legacy["observed_identity"].pop("object_kind")
    parsed = SourceIdentityRecord.from_dict(legacy)
    assert parsed.observed_identity.object_kind == "blob"
