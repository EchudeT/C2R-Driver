from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from driver_port_factory.codex.prompts import STAGE_DOCUMENTS, SkillPromptComposer
from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    ProjectConfig,
    StageOwner,
)
from driver_port_factory.core.workflow import workflow_for


class SkillPromptTests(unittest.TestCase):
    def test_prompt_embeds_exact_english_skill_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected: dict[str, str] = {}
            for relative in STAGE_DOCUMENTS["environment_recovery"]:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                content = f"English normative source: {relative}\n"
                path.write_text(content, encoding="utf-8")
                expected[relative] = content
            rendered = SkillPromptComposer(root).render(
                stage="environment_recovery",
                actor_role=ActorRole.DEVELOPER,
                objective="分析用户提供的中文迁移需求",
                context={"original_user_request": "迁移这个驱动"},
            )
            self.assertIn("Do not translate, summarize, paraphrase", rendered.text)
            self.assertIn("迁移这个驱动", rendered.text)
            self.assertEqual(hashlib.sha256(rendered.text.encode()).hexdigest(), rendered.digest)
            for document in rendered.documents:
                self.assertEqual(document.content, expected[document.relative_path])
                self.assertEqual(
                    document.digest,
                    hashlib.sha256(expected[document.relative_path].encode()).hexdigest(),
                )
                self.assertIn(expected[document.relative_path], rendered.text)

    def test_every_non_static_stage_has_an_upstream_skill_mapping(self) -> None:
        configurations = (
            (ActorRole.DEVELOPER, EvaluationMode.DEVELOPER_EVIDENCE),
            (ActorRole.MIGRATION_OPERATOR, EvaluationMode.PROSPECTIVE_BLIND),
            (ActorRole.CURATOR, EvaluationMode.PROSPECTIVE_BLIND),
            (ActorRole.CURATOR, EvaluationMode.POST_HOC_SEALED_BLIND),
            (ActorRole.EVALUATOR, EvaluationMode.PROSPECTIVE_BLIND),
            (ActorRole.AUDITOR, EvaluationMode.PROSPECTIVE_BLIND),
        )
        missing: list[str] = []
        for role, mode in configurations:
            config = ProjectConfig(
                project_id=f"{role.value}-{mode.value}",
                source_platform="source",
                target_platform="target",
                driver_name="arbitrary-driver",
                evaluation_mode=mode,
                actor_role=role,
            )
            for stage in workflow_for(config):
                if stage.owner is not StageOwner.STATIC and stage.name not in STAGE_DOCUMENTS:
                    missing.append(f"{role.value}:{stage.name}")
        self.assertEqual(missing, [])

    def test_development_prompt_sources_may_change_between_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = STAGE_DOCUMENTS["driver_candidate_resolution"]
            for relative in paths:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"first version of {relative}\n", encoding="utf-8")
            composer = SkillPromptComposer(root)
            first = composer.render(
                stage="driver_candidate_resolution",
                actor_role=ActorRole.DEVELOPER,
                objective="Resolve an arbitrary driver identity",
            )
            changed = root / paths[-1]
            changed.write_text("second version of the intake rules\n", encoding="utf-8")
            second = composer.render(
                stage="driver_candidate_resolution",
                actor_role=ActorRole.DEVELOPER,
                objective="Resolve an arbitrary driver identity",
            )
            self.assertNotEqual(first.digest, second.digest)
            self.assertNotEqual(first.documents[-1].digest, second.documents[-1].digest)


if __name__ == "__main__":
    unittest.main()
