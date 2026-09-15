from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..acquisition.models import CheckoutRecord, RepositoryRole
from ..core.models import ActorRole, ArtifactDirection, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from .contracts import EnvironmentArtifact, EnvironmentStage, RouteDiscoveryStatus
from .documents import json_artifact
from .evidence import checkout_for, workspace_path
from .models import ArtifactMode


class EnvironmentInspector:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)
    TOOLS = (
        "qemu-system-x86_64",
        "qemu-system-aarch64",
        "qemu-system-riscv64",
        "qemu-storage-daemon",
        "docker",
        "podman",
        "cargo",
        "rustc",
        "clang",
        "cmake",
        "make",
        "ninja",
        "git",
    )

    def inspect(self, project: Project) -> dict[str, Any]:
        project.ensure_role(*self.ROLES)
        stage = project.stage(EnvironmentStage.RECOVERY)
        if stage.status is StageStatus.READY:
            project.start(EnvironmentStage.RECOVERY)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(
                f"environment_recovery must be READY or RUNNING, got {stage.status.value}"
            )
        acquisition = project.load_json_artifact(
            AcquisitionStage.EVIDENCE_ACQUISITION,
            AcquisitionArtifact.ACQUISITION_MANIFEST,
        )
        checkouts = tuple(CheckoutRecord.from_dict(record) for record in acquisition["checkouts"])
        target = checkout_for(checkouts, RepositoryRole.TARGET)
        qemu = checkout_for(checkouts, RepositoryRole.QEMU)
        metadata = self._operational_metadata(workspace_path(project, target.checkout_path))
        tools: list[dict[str, Any]] = []
        for name in self.TOOLS:
            path = shutil.which(name)
            tools.append({"name": name, "path": path, "available": path is not None})
        inventory = self._inventory(project, target, qemu, metadata, tools)
        candidates = {
            "schema_version": 1,
            "recorded_at": utc_now(),
            "selection_rule": (
                "choose the least expensive reproducible route that can contain the migrated "
                "driver; discovery is evidence, not proof that a route works"
            ),
            "candidates": self._candidates(
                metadata,
                tools,
                workspace_path(project, qemu.checkout_path),
                project,
            ),
        }
        for artifact in (
            json_artifact(EnvironmentArtifact.INVENTORY, inventory),
            json_artifact(EnvironmentArtifact.MODE_CANDIDATES, candidates),
        ):
            project.record_artifact(
                EnvironmentStage.RECOVERY,
                artifact,
                direction=ArtifactDirection.INPUT,
            )
        return {"inventory": inventory, "artifact_mode_candidates": candidates}

    @staticmethod
    def _inventory(
        project: Project,
        target: CheckoutRecord,
        qemu: CheckoutRecord,
        metadata: list[str],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        disk = shutil.disk_usage(project.root)
        return {
            "schema_version": 1,
            "recorded_at": utc_now(),
            "host": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": sys.version.split()[0],
                "filesystem_device": os.stat(project.root).st_dev,
            },
            "disk": {
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
            },
            "tools": tools,
            "frozen_repositories": {
                "target": {
                    "path": target.checkout_path,
                    "revision": target.resolved_commit,
                    "tree_id": target.tree_id,
                },
                "qemu": {
                    "path": qemu.checkout_path,
                    "revision": qemu.resolved_commit,
                    "tree_id": qemu.tree_id,
                },
            },
            "existing_operational_metadata": metadata,
        }

    @staticmethod
    def _operational_metadata(target_root: Path) -> list[str]:
        markers = (
            "makefile",
            "cargo.toml",
            "justfile",
            "taskfile",
            "dockerfile",
            "devcontainer",
            "workflow",
            "gitlab-ci",
            "qemu",
            "runner",
            "run.sh",
            "boot",
            "image",
            "initramfs",
            "repack",
            "package",
            "component",
            "module",
            "sdk",
            "release",
        )
        matches: list[str] = []
        for path in target_root.rglob("*"):
            relative = path.relative_to(target_root).as_posix()
            if path.is_file() and any(marker in relative.lower() for marker in markers):
                matches.append(relative)
            if len(matches) >= 500:
                break
        return sorted(matches)

    @staticmethod
    def _candidates(
        metadata: list[str],
        tools: list[dict[str, Any]],
        qemu_root: Path,
        project: Project,
    ) -> list[dict[str, Any]]:
        rules = (
            (
                ArtifactMode.VERIFIED_LOCAL_RUNNER,
                ("runner", "run.sh", "qemu"),
                "versioned target runner or QEMU helper",
            ),
            (
                ArtifactMode.OFFICIAL_CONTAINER_OR_SDK,
                ("dockerfile", "devcontainer", "container", "sdk"),
                "official container or SDK metadata",
            ),
            (
                ArtifactMode.PREBUILT_COMPONENT_INSERTION,
                ("component", "module", "package"),
                "component/module/package insertion metadata",
            ),
            (
                ArtifactMode.IMAGE_REPACK,
                ("repack", "initramfs", "image"),
                "image overlay or repack metadata",
            ),
            (
                ArtifactMode.CI_DERIVED_BUILD,
                ("workflow", "gitlab-ci", ".github"),
                "versioned CI/release automation",
            ),
            (
                ArtifactMode.SOURCE_BUILD,
                ("makefile", "cargo.toml", "justfile", "taskfile"),
                "source build metadata; use only if lighter routes cannot contain the driver",
            ),
        )
        normalized_metadata = tuple((path, path.lower()) for path in metadata)
        candidates: list[dict[str, Any]] = []
        for mode, tokens, reason in rules:
            evidence_paths = [
                path
                for path, normalized_path in normalized_metadata
                if any(token in normalized_path for token in tokens)
            ]
            if not evidence_paths:
                continue
            candidates.append(
                {
                    "artifact_mode": mode,
                    "reason": reason,
                    "evidence_paths": evidence_paths[:50],
                    "status": RouteDiscoveryStatus.DISCOVERED_NOT_EXECUTED,
                }
            )
        qemu_tools = [
            item for item in tools if item["available"] and item["name"].startswith("qemu-")
        ]
        if qemu_tools:
            candidates.append(
                {
                    "artifact_mode": ArtifactMode.DIRECT_DEVICE_MODEL,
                    "reason": (
                        "host QEMU binary plus frozen QEMU model source can support a direct smoke"
                    ),
                    "evidence_paths": [
                        str(qemu_root.relative_to(project.root)),
                        *[item["path"] for item in qemu_tools],
                    ],
                    "status": RouteDiscoveryStatus.DISCOVERED_NOT_EXECUTED,
                }
            )
        return candidates
