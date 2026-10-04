import json
import shutil


def test_composite_repair_targets_existing_delivery_without_restarting(tmp_path):
    from driver_port_factory.codex.prompts import SkillPromptComposer, default_prompt_pack_path
    from driver_port_factory.composition import WORKFLOW_STAGE_CATALOG
    from driver_port_factory.core.models import ActorRole
    from driver_port_factory.migration.contracts import MigrationStage

    prompt_pack = tmp_path / "prompt-pack"
    shutil.copytree(default_prompt_pack_path(), prompt_pack)
    skill_root = tmp_path / "skill"
    for relative in (
        "knowledge-guided-driver-port/SKILL.md",
        "knowledge-guided-driver-port/references/workflow.md",
        "knowledge-guided-driver-port/references/translation.md",
        "knowledge-guided-driver-port/references/target-changes.md",
        "knowledge-guided-driver-port/references/knowledge-contract.md",
        "knowledge-guided-driver-port/references/test-porting.md",
        "knowledge-guided-driver-port/references/qemu-evidence.md",
    ):
        path = skill_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n", encoding="utf-8")

    composer = SkillPromptComposer(
        skill_root,
        WORKFLOW_STAGE_CATALOG,
        (MigrationStage.DRIVER_IMPLEMENTATION.value,),
        prompt_pack,
    )
    from driver_port_factory.short_refs import References

    (tmp_path / ".dpf").mkdir()
    provenance = {"path": "source.c", "sha256": "a" * 64}
    rendered = composer.render(
        stage=MigrationStage.DRIVER_IMPLEMENTATION,
        actor_role=ActorRole.DEVELOPER,
        context={
            "tool_runtime": {"project_root": str(tmp_path)},
            "source": provenance,
            "repair_execution": {
                "mode": "prepare-once-controller-validates",
                "completion": "repair complete",
            },
        },
    )
    job = json.loads(rendered.text.split("<job>", 1)[1].split("</job>", 1)[0])
    assert "a" * 64 not in rendered.text
    assert (
        References(tmp_path).get(job["reference_material"]["source"]["evidence_ref"]) == provenance
    )
    instructions = job["instructions"]
    assert "Repair the recorded defect" in instructions["objective"]
    assert "existing delivery scope remains required" in instructions["objective"]
    assert instructions["delivery_repair"].startswith("# Targeted repair")
    assert "runtime-artifact" in instructions["delivery_repair"]

    # A repair may not replace the selected behavior with a whole-delivery objective/protocol.
    rendered = composer.render(
        stage=MigrationStage.DRIVER_IMPLEMENTATION,
        actor_role=ActorRole.DEVELOPER,
        context={
            "behavior_progress": {
                "objective": "Repair the selected registration behavior only.",
                "current": {"id": "registration"},
                "plan_exists": True,
            },
            "repair_execution": {"completion": "submit the entire delivery pass"},
        },
    )
    scoped = json.loads(rendered.text.split("<job>", 1)[1].split("</job>", 1)[0])["instructions"]
    assert scoped["objective"] == "Repair the selected registration behavior only."
    assert "behavior_continue" in scoped["protocol"]["completion"]
    assert "No worker-requested controller operations" not in scoped["protocol"]["operation_delivery"]


def test_public_qemu_objective_consumes_artifact_preparation_output():
    from driver_port_factory.codex.prompts import default_prompt_pack_path

    manifest = json.loads((default_prompt_pack_path() / "manifest.json").read_text())
    objective = manifest["stages"]["public_qemu_validation"]["objective"]
    assert "Consume the artifact and identity fixed by artifact_preparation" in objective
    assert "Prepare the runtime artifact" not in objective


def test_prompt_uses_file_submission_without_legacy_state_channel(tmp_path):
    from driver_port_factory.codex.prompts import SkillPromptComposer, default_prompt_pack_path
    from driver_port_factory.composition import WORKFLOW_STAGE_CATALOG
    from driver_port_factory.core.models import ActorRole
    from driver_port_factory.migration.contracts import MigrationStage

    prompt_pack = tmp_path / "prompt-pack"
    shutil.copytree(default_prompt_pack_path(), prompt_pack)
    skill_root = tmp_path / "skill"
    for relative in (
        "knowledge-guided-driver-port/SKILL.md",
        "knowledge-guided-driver-port/references/workflow.md",
        "knowledge-guided-driver-port/references/translation.md",
        "knowledge-guided-driver-port/references/knowledge-contract.md",
        "knowledge-guided-driver-port/references/test-porting.md",
        "knowledge-guided-driver-port/references/qemu-evidence.md",
        "knowledge-guided-driver-port/references/target-changes.md",
    ):
        path = skill_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    rendered = SkillPromptComposer(
        skill_root,
        WORKFLOW_STAGE_CATALOG,
        (MigrationStage.CONTRACTS.value,),
        prompt_pack,
    ).render(stage=MigrationStage.CONTRACTS, actor_role=ActorRole.DEVELOPER)
    payload = json.loads(rendered.text.split("<job>", 1)[1].split("</job>", 1)[0])
    assert "output_schema" not in payload["instructions"]
    assert "REPORT_PATH" not in rendered.text
    assert "DPF_STATUS" not in rendered.text
    assert "DPF_REVIEW" not in rendered.text
    assert "submission_command" in rendered.text
