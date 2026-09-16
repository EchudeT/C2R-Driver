from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from driver_port_factory.codex.prompts import SkillPromptComposer, load_prompt_pack
from driver_port_factory.composition import WORKFLOW_STAGE_CATALOG, workflow_for
from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    ProjectConfig,
    StageOwner,
    WorkflowError,
)
from driver_port_factory.environment.contracts import EnvironmentStage
from driver_port_factory.intake.contracts import IntakeStage


def write_prompt_pack(
    root: Path,
    *,
    wrapper: str,
    stages: dict[str, list[str]],
    objectives: dict[str, str] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "job.md").write_text(wrapper, encoding="utf-8")
    (root / "correction.md").write_text("Rejected: {{error}}\n", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "test-pack",
                "template": "job.md",
                "correction_template": "correction.md",
                "stages": {
                    stage: {
                        "documents": documents,
                        **(
                            {"objective": objectives[stage]}
                            if objectives and stage in objectives
                            else {}
                        ),
                    }
                    for stage, documents in stages.items()
                },
            }
        ),
        encoding="utf-8",
    )
    return root


class SkillPromptTests(unittest.TestCase):
    def test_prompt_uses_current_skill_and_editable_wrapper_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill_root = root / "skills"
            documents = [
                "open-kernel-driver-port/SKILL.md",
                "open-kernel-driver-port/references/environment-recovery.md",
            ]
            expected: dict[str, str] = {}
            for relative in documents:
                path = skill_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                content = f"可编辑的规范来源：{relative}\n"
                path.write_text(content, encoding="utf-8")
                expected[relative] = content
            prompt_pack = write_prompt_pack(
                root / "prompt-pack",
                wrapper="自定义阶段指令\n<job>{{job_json}}</job>\n{{skill_documents}}\n",
                stages={"environment_recovery": documents},
            )
            rendered = SkillPromptComposer(
                skill_root,
                WORKFLOW_STAGE_CATALOG,
                (EnvironmentStage.RECOVERY.value,),
                prompt_pack,
            ).render(
                stage=EnvironmentStage.RECOVERY,
                actor_role=ActorRole.DEVELOPER,
                objective="分析用户提供的中文迁移需求",
                context={"original_user_request": "迁移这个驱动"},
            )
            self.assertIn("自定义阶段指令", rendered.text)
            self.assertIn("迁移这个驱动", rendered.text)
            self.assertEqual(hashlib.sha256(rendered.text.encode()).hexdigest(), rendered.digest)
            for document in rendered.documents:
                self.assertEqual(document.content, expected[document.relative_path])
                self.assertIn(expected[document.relative_path], rendered.text)

    def test_prompt_uses_editable_stage_objective_when_caller_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill_root = root / "skills"
            relative = "open-kernel-driver-port/SKILL.md"
            skill = skill_root / relative
            skill.parent.mkdir(parents=True)
            skill.write_text("skill\n", encoding="utf-8")
            prompt_pack = write_prompt_pack(
                root / "prompt-pack",
                wrapper="{{job_json}}\n{{skill_documents}}\n",
                stages={EnvironmentStage.RECOVERY.value: [relative]},
                objectives={EnvironmentStage.RECOVERY.value: "Editable pack objective"},
            )

            rendered = SkillPromptComposer(
                skill_root,
                WORKFLOW_STAGE_CATALOG,
                (EnvironmentStage.RECOVERY.value,),
                prompt_pack,
            ).render(
                stage=EnvironmentStage.RECOVERY,
                actor_role=ActorRole.DEVELOPER,
            )

            self.assertEqual(rendered.objective, "Editable pack objective")
            self.assertIn('"objective": "Editable pack objective"', rendered.text)

    def test_default_pack_maps_every_non_static_stage(self) -> None:
        configurations = (
            (ActorRole.DEVELOPER, EvaluationMode.DEVELOPER_EVIDENCE),
            (ActorRole.MIGRATION_OPERATOR, EvaluationMode.PROSPECTIVE_BLIND),
            (ActorRole.CURATOR, EvaluationMode.PROSPECTIVE_BLIND),
            (ActorRole.CURATOR, EvaluationMode.POST_HOC_SEALED_BLIND),
            (ActorRole.EVALUATOR, EvaluationMode.PROSPECTIVE_BLIND),
            (ActorRole.AUDITOR, EvaluationMode.PROSPECTIVE_BLIND),
        )
        for role, mode in configurations:
            config = ProjectConfig(
                project_id=f"{role.value}-{mode.value}",
                source_platform="source",
                target_platform="target",
                driver_name="arbitrary-driver",
                evaluation_mode=mode,
                actor_role=role,
            )
            definition = workflow_for(config)
            prompt_pack = load_prompt_pack(None, WORKFLOW_STAGE_CATALOG)
            missing: list[str] = []
            for stage in definition.stages:
                if (
                    stage.owner is not StageOwner.STATIC
                    and stage.name.value not in prompt_pack.stages
                ):
                    missing.append(f"{role.value}:{stage.name.value}")
            self.assertEqual(missing, [])

    def test_default_pack_binds_structured_stage_schemas(self) -> None:
        prompt_pack = load_prompt_pack(None, WORKFLOW_STAGE_CATALOG)
        expected = {
            "revision_selection": "revision-selection-proposal.schema.json",
            "evidence_closure": "evidence-closure-proposal.schema.json",
        }
        actual = {
            stage: specification.output_schema.relative_path
            for stage, specification in prompt_pack.stages.items()
            if specification.output_schema is not None
        }
        self.assertEqual(actual, expected)

    def test_legacy_stage_document_list_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "prompt-pack"
            root.mkdir()
            (root / "job.md").write_text(
                "{{job_json}}\n{{skill_documents}}\n",
                encoding="utf-8",
            )
            (root / "correction.md").write_text("Rejected: {{error}}\n", encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "legacy-pack",
                        "template": "job.md",
                        "correction_template": "correction.md",
                        "stages": {"driver_candidate_resolution": ["SKILL.md"]},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowError, "stage specification"):
                load_prompt_pack(root, WORKFLOW_STAGE_CATALOG)

    def test_prompt_sources_and_wrapper_may_change_between_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill_root = root / "skills"
            relative = "open-kernel-driver-port/SKILL.md"
            skill = skill_root / relative
            skill.parent.mkdir(parents=True)
            skill.write_text("first skill version\n", encoding="utf-8")
            prompt_pack = write_prompt_pack(
                root / "prompt-pack",
                wrapper="version one\n{{job_json}}\n{{skill_documents}}\n",
                stages={"driver_candidate_resolution": [relative]},
            )
            first = SkillPromptComposer(
                skill_root,
                WORKFLOW_STAGE_CATALOG,
                (IntakeStage.CANDIDATE_RESOLUTION.value,),
                prompt_pack,
            ).render(
                stage=IntakeStage.CANDIDATE_RESOLUTION,
                actor_role=ActorRole.DEVELOPER,
                objective="Resolve an arbitrary driver identity",
            )
            skill.write_text("second skill version\n", encoding="utf-8")
            (prompt_pack / "job.md").write_text(
                "version two\n{{job_json}}\n{{skill_documents}}\n",
                encoding="utf-8",
            )
            second = SkillPromptComposer(
                skill_root,
                WORKFLOW_STAGE_CATALOG,
                (IntakeStage.CANDIDATE_RESOLUTION.value,),
                prompt_pack,
            ).render(
                stage=IntakeStage.CANDIDATE_RESOLUTION,
                actor_role=ActorRole.DEVELOPER,
                objective="Resolve an arbitrary driver identity",
            )
            self.assertNotEqual(first.digest, second.digest)
            self.assertNotEqual(first.prompt_template_digest, second.prompt_template_digest)
            self.assertNotEqual(first.documents[0].digest, second.documents[0].digest)

    def test_prompt_pack_cannot_silently_drop_skill_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prompt_pack = write_prompt_pack(
                root / "prompt-pack",
                wrapper="{{job_json}}\n",
                stages={"driver_candidate_resolution": ["open-kernel-driver-port/SKILL.md"]},
            )
            with self.assertRaisesRegex(WorkflowError, "skill_documents"):
                load_prompt_pack(prompt_pack, WORKFLOW_STAGE_CATALOG)

    def test_current_workflow_limits_rendering_without_weakening_pack_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill_root = root / "skills"
            document = "open-kernel-driver-port/SKILL.md"
            path = skill_root / document
            path.parent.mkdir(parents=True)
            path.write_text("skill\n", encoding="utf-8")
            prompt_pack = write_prompt_pack(
                root / "prompt-pack",
                wrapper="{{job_json}}\n{{skill_documents}}\n",
                stages={
                    EnvironmentStage.RECOVERY.value: [document],
                    IntakeStage.CANDIDATE_RESOLUTION.value: [document],
                },
            )
            composer = SkillPromptComposer(
                skill_root,
                WORKFLOW_STAGE_CATALOG,
                (EnvironmentStage.RECOVERY.value,),
                prompt_pack,
            )
            with self.assertRaisesRegex(WorkflowError, "outside the current project workflow"):
                composer.render(
                    stage=IntakeStage.CANDIDATE_RESOLUTION,
                    actor_role=ActorRole.DEVELOPER,
                    objective="must not render",
                )

    def test_prompt_manifest_unknown_stage_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prompt_pack = write_prompt_pack(
                Path(temporary) / "prompt-pack",
                wrapper="{{job_json}}\n{{skill_documents}}\n",
                stages={"misspelled_stage": ["SKILL.md"]},
            )
            with self.assertRaisesRegex(WorkflowError, "unknown stage"):
                load_prompt_pack(prompt_pack, WORKFLOW_STAGE_CATALOG)


if __name__ == "__main__":
    unittest.main()
