import json
import shutil


def test_composite_repair_keeps_the_stage_objective(tmp_path):
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
    rendered = composer.render(
        stage=MigrationStage.DRIVER_IMPLEMENTATION,
        actor_role=ActorRole.DEVELOPER,
        context={
            "repair_execution": {
                "mode": "prepare-once-controller-validates",
                "completion": "repair complete",
            }
        },
    )
    job = json.loads(rendered.text.split("<job>", 1)[1].split("</job>", 1)[0])
    instructions = job["instructions"]
    assert "phases 6–7" in instructions["objective"]
    assert instructions["repair_task"].startswith("# Composite repair task")
    assert "runtime-artifact" in instructions["repair_task"]


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
        skill_root, WORKFLOW_STAGE_CATALOG,
        (MigrationStage.CONTRACTS.value,), prompt_pack,
    ).render(stage=MigrationStage.CONTRACTS, actor_role=ActorRole.DEVELOPER)
    payload = json.loads(rendered.text.split("<job>", 1)[1].split("</job>", 1)[0])
    assert "output_schema" not in payload["instructions"]
    assert "REPORT_PATH" not in rendered.text
    assert "DPF_STATUS" not in rendered.text
    assert "DPF_REVIEW" not in rendered.text
    assert "submission_command" in rendered.text
