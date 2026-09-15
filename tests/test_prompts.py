from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from driver_port_factory.codex.prompts import SkillPromptComposer
from driver_port_factory.core.models import ActorRole


class PromptComposerTests(unittest.TestCase):
    def test_original_skill_text_and_digests_are_embedded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "open-kernel-driver-port"
            (skill / "references").mkdir(parents=True)
            skill_text = "# Original Skill\nKeep the intake gate.\n"
            reference_text = "# Intake\nConfirm one PCI driver.\n"
            (skill / "SKILL.md").write_text(skill_text, encoding="utf-8")
            (skill / "references" / "intake.md").write_text(reference_text, encoding="utf-8")

            rendered = SkillPromptComposer(root).render(
                stage="driver_identity",
                actor_role=ActorRole.MIGRATION_OPERATOR,
                objective="Resolve NE2000 identity",
            )
            self.assertIn(skill_text, rendered.text)
            self.assertIn(reference_text, rendered.text)
            expected = hashlib.sha256(skill_text.encode()).hexdigest()
            self.assertIn(expected, rendered.text)
            self.assertEqual(hashlib.sha256(rendered.text.encode()).hexdigest(), rendered.digest)


if __name__ == "__main__":
    unittest.main()
