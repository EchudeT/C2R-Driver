from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from driver_port_factory.acquisition.contracts import AcquisitionStage
from driver_port_factory.codex.contracts import CodexBackend, CodexContinuation, CodexOutputError
from driver_port_factory.knowledge.contracts import KnowledgeStage
from driver_port_factory.port import PortOptions, PortRunner
from driver_port_factory.source_analysis.clang_backend import AnalyzerFamily


def runner() -> PortRunner:
    return PortRunner(
        PortOptions(
            workspace=Path("/workspace"),
            source_platform="linux",
            target_platform="asterinas",
            driver_name="ne2k-pci",
            skill_root=Path("/skills"),
            catalogs=(),
            backend=CodexBackend.EXEC,
            codex_bin="fake-codex",
            model=None,
            analyzer="fake-clang",
            analyzer_family=AnalyzerFamily.CLANG_LLVM,
        )
    )


class PortEvidenceContractTests(unittest.TestCase):
    def test_execution_receipts_continue_without_consuming_corrections(self) -> None:
        port = runner()
        accept = Mock(side_effect=[CodexContinuation(f"receipt {i}") for i in range(3)] + [None])
        with (
            patch.object(port, "_latest_job_occurrence", return_value=None),
            patch.object(port, "_codex", return_value=(SimpleNamespace(thread_id="same-thread"), None, Path("response"))) as codex,
            patch.object(port, "_job_occurrence", return_value=object()),
        ):
            self.assertTrue(port._codex_gate(object(), KnowledgeStage.KNOWLEDGE_BASE, {}, accept))
        self.assertEqual(codex.call_count, 4)
        self.assertEqual(codex.call_args.args[2]["controller_execution"], "receipt 2")
        self.assertIsNone(codex.call_args.kwargs["follow_up"])
        self.assertEqual(codex.call_args.kwargs["thread_id"], "same-thread")

    def test_evidence_stage_uses_prompt_pack_without_hidden_override(self) -> None:
        port = runner()
        with (
            patch.object(port, "_artifact_context", return_value={"digest": "a" * 64}),
            patch.object(port, "_codex_gate") as codex_gate,
        ):
            port._evidence(SimpleNamespace(artifact_refs=lambda **_: []))

        self.assertEqual(codex_gate.call_args.args[1], AcquisitionStage.EVIDENCE_CLOSURE)
        self.assertNotIn("objective", codex_gate.call_args.kwargs)

    def test_same_compact_contract_is_used_for_correction(self) -> None:
        port = runner()
        result = SimpleNamespace(thread_id="same-thread")
        attempts = 0

        def accept(_project, _job) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise CodexOutputError("invalid proposal")

        with (
            patch.object(port, "_latest_thread_id", return_value=None),
            patch.object(port, "_latest_job_occurrence", return_value=None),
            patch.object(
                port,
                "_codex",
                return_value=(result, None, Path("response")),
            ) as codex,
            patch.object(port, "_job_occurrence", return_value=object()),
        ):
            port._codex_gate(
                object(),
                KnowledgeStage.KNOWLEDGE_BASE,
                {},
                accept,
                objective="current compact contract",
            )

        self.assertEqual(codex.call_count, 2)
        self.assertEqual(codex.call_args.kwargs["thread_id"], "same-thread")
        self.assertEqual(codex.call_args.kwargs["follow_up"], "invalid proposal")
        self.assertEqual(codex.call_args.kwargs["objective"], "current compact contract")



if __name__ == "__main__":
    unittest.main()
