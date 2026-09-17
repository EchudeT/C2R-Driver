from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from driver_port_factory.acquisition.contracts import AcquisitionStage
from driver_port_factory.acquisition.proposal import CODEX_EVIDENCE_SELECTION_OBJECTIVE
from driver_port_factory.codex.contracts import CodexBackend, CodexOutputError
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
    def test_evidence_stage_overrides_prompt_pack_with_semantic_contract(self) -> None:
        port = runner()
        with (
            patch.object(port, "_artifact_context", return_value={"digest": "a" * 64}),
            patch.object(port, "_codex_gate") as codex_gate,
        ):
            port._evidence(object())

        self.assertEqual(codex_gate.call_args.args[1], AcquisitionStage.EVIDENCE_CLOSURE)
        self.assertEqual(
            codex_gate.call_args.kwargs["objective"],
            CODEX_EVIDENCE_SELECTION_OBJECTIVE,
        )

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
