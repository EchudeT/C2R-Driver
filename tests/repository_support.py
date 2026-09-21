from __future__ import annotations


import json


from pathlib import Path


from driver_port_factory.acquisition.repository_role import RepositoryRole


from driver_port_factory.composition import initialize_project


from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    ProjectConfig,
)


from driver_port_factory.intake.service import IntakeService


from tests.acquisition_support import (
    git,
    repository,
    select_revisions,
)


def project_config() -> ProjectConfig:
    return ProjectConfig(
        project_id="acquisition-test",
        source_platform="example-source",
        target_platform="example-target",
        driver_name="example-driver",
        evaluation_mode=EvaluationMode.DEVELOPER_EVIDENCE,
        actor_role=ActorRole.DEVELOPER,
    )


def ready_project(
    root: Path,
    *,
    source_overrides: dict[str, str] | None = None,
    target_overrides: dict[str, str] | None = None,
):
    source = repository(
        root,
        "source",
        source_overrides
        or {
            "drivers/example.c": "/* source driver */\n",
            "docs/original.txt": "original source contract\n",
            "docs/derived.txt": "derived source contract\n",
        },
    )
    target = repository(root, "target", target_overrides or {"README.md": "target\n"})
    qemu = repository(root, "qemu", {"hw/example.c": "/* model */\n"})
    catalog = root / "drivers.json"
    catalog.write_text(
        json.dumps(
            {
                "source_platform": "example-source",
                "drivers": [
                    {
                        "candidate_id": "example-driver",
                        "canonical_name": "example-driver",
                        "source_entry_hint": "drivers/example.c",
                        "device_family": "Example device",
                        "bus_or_transport": "TESTBUS",
                        "aliases": [],
                        "device_scope": ["Example device"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    project = initialize_project(root / "run", project_config())
    IntakeService().analyze(project, raw_request="port example", catalog_paths=(catalog,))
    for path in (source, target, qemu):
        git("tag", "v1.0.0", cwd=path)
    select_revisions(
        project,
        source,
        target,
        qemu,
        requested_refs={role: "v1.0.0" for role in RepositoryRole},
    )
    return project, source, target, qemu
