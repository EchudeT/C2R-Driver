from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from driver_port_factory.composition import ARTIFACT_VALIDATORS
from driver_port_factory.core.models import StageStatus, WorkflowError
from driver_port_factory.migration.contracts import MigrationStage
from driver_port_factory.source_analysis.ast_index import AstSemanticIndexer
from driver_port_factory.source_analysis.closure import SourceClosureService
from driver_port_factory.source_analysis.contracts import (
    SourceAnalysisArtifact,
    SourceAnalysisStage,
)
from driver_port_factory.source_analysis.fact_parsers import RawFactParser
from driver_port_factory.source_analysis.semantic_model import CallDispatch, RelationKind
from driver_port_factory.source_analysis.structured import StructuredCAnalysisService
from tests.test_source_closure import ready_project, source_closure, write_json


class StructuredCAnalysisTests(unittest.TestCase):
    def test_cfg_identity_uses_ast_candidates_for_callback_declarators(self) -> None:
        functions = [
            {
                "signature": "int f(int (*callback)(int), int value)",
                "ast_node_id": None,
                "blocks": [],
            },
            {
                "signature": "int callback(int value)",
                "ast_node_id": None,
                "blocks": [],
            },
        ]
        identities = [
            {"node_id": "ast:f", "name": "f"},
            {"node_id": "ast:callback", "name": "callback"},
        ]

        RawFactParser._correlate_functions(functions, identities)

        self.assertEqual(functions[0]["ast_node_id"], "ast:f")
        self.assertEqual(functions[1]["ast_node_id"], "ast:callback")

    def test_cfg_identity_handles_function_returning_function_pointer(self) -> None:
        functions = [
            {
                "signature": "int (*maker(void))(int)",
                "ast_node_id": None,
                "blocks": [],
            }
        ]

        RawFactParser._correlate_functions(
            functions,
            [{"node_id": "ast:maker", "name": "maker"}],
        )

        self.assertEqual(functions[0]["ast_node_id"], "ast:maker")

    def test_clang_registration_table_initializer_resolves_indirect_call(self) -> None:
        clang = shutil.which("clang")
        if not clang:
            self.skipTest("test requires clang")
        source = """
            struct operations { int (*run)(int); };
            static int callback(int value) { return value; }
            static const struct operations table = { .run = callback };
            int invoke(int value) { return table.run(value); }
        """
        completed = subprocess.run(
            [
                clang,
                "-xc",
                "-std=gnu11",
                "-Xclang",
                "-ast-dump=json",
                "-fsyntax-only",
                "-",
            ],
            input=source,
            check=True,
            capture_output=True,
            text=True,
        )

        semantic = AstSemanticIndexer(
            "registration-table", Path("registration-table.c"), json.loads(completed.stdout)
        ).build()

        indirect_calls = [
            call
            for call in semantic["indexes"]["calls"]
            if call["dispatch"] is not CallDispatch.DIRECT
        ]
        self.assertEqual(len(indirect_calls), 1)
        self.assertEqual(indirect_calls[0]["dispatch"], CallDispatch.INDIRECT_RESOLVED)
        self.assertEqual(len(indirect_calls[0]["candidate_target_ids"]), 1)
        self.assertEqual(semantic["counts"]["unresolved_indirect_calls"], 0)

    def test_function_pointer_targets_are_structured_and_unresolved_calls_are_explicit(
        self,
    ) -> None:
        function = {
            "id": "function",
            "kind": "FunctionDecl",
            "name": "callback",
            "type": {"qualType": "int (int)"},
            "loc": {"line": 1, "col": 1},
            "inner": [{"id": "body", "kind": "CompoundStmt"}],
        }
        pointer = {
            "id": "pointer",
            "kind": "VarDecl",
            "name": "callback_pointer",
            "storageClass": "static",
            "type": {"qualType": "int (*const)(int)"},
            "loc": {"line": 2, "col": 1},
            "inner": [
                {
                    "id": "decay",
                    "kind": "ImplicitCastExpr",
                    "castKind": "FunctionToPointerDecay",
                    "type": {"qualType": "int (*const)(int)"},
                    "inner": [
                        {
                            "id": "function-reference",
                            "kind": "DeclRefExpr",
                            "referencedDecl": {
                                "id": "function",
                                "kind": "FunctionDecl",
                                "name": "callback",
                                "type": {"qualType": "int (int)"},
                            },
                        }
                    ],
                }
            ],
        }
        resolved_call = {
            "id": "resolved-call",
            "kind": "CallExpr",
            "type": {"qualType": "int"},
            "loc": {"line": 3, "col": 1},
            "inner": [
                {
                    "id": "load-pointer",
                    "kind": "ImplicitCastExpr",
                    "castKind": "LValueToRValue",
                    "type": {"qualType": "int (*const)(int)"},
                    "inner": [
                        {
                            "id": "pointer-reference",
                            "kind": "DeclRefExpr",
                            "type": {"qualType": "int (*const)(int)"},
                            "referencedDecl": {
                                "id": "pointer",
                                "kind": "VarDecl",
                                "name": "callback_pointer",
                                "type": {"qualType": "int (*const)(int)"},
                            },
                        }
                    ],
                }
            ],
        }
        unresolved_parameter = {
            "id": "parameter",
            "kind": "ParmVarDecl",
            "name": "unknown_callback",
            "type": {"qualType": "int (*)(int)"},
            "loc": {"line": 4, "col": 1},
        }
        unresolved_call = {
            "id": "unresolved-call",
            "kind": "CallExpr",
            "type": {"qualType": "int"},
            "loc": {"line": 5, "col": 1},
            "inner": [
                {
                    "id": "load-parameter",
                    "kind": "ImplicitCastExpr",
                    "castKind": "LValueToRValue",
                    "type": {"qualType": "int (*)(int)"},
                    "inner": [
                        {
                            "id": "parameter-reference",
                            "kind": "DeclRefExpr",
                            "type": {"qualType": "int (*)(int)"},
                            "referencedDecl": {
                                "id": "parameter",
                                "kind": "ParmVarDecl",
                                "name": "unknown_callback",
                                "type": {"qualType": "int (*)(int)"},
                            },
                        }
                    ],
                }
            ],
        }
        ast = {
            "id": "translation-unit",
            "kind": "TranslationUnitDecl",
            "inner": [function, pointer, resolved_call, unresolved_parameter, unresolved_call],
        }

        semantic = AstSemanticIndexer("callbacks", Path("callbacks.c"), ast).build()

        calls = semantic["indexes"]["calls"]
        self.assertEqual(calls[0]["dispatch"], CallDispatch.INDIRECT_RESOLVED)
        self.assertEqual(calls[0]["candidate_target_ids"], ["callbacks:0"])
        self.assertEqual(calls[1]["dispatch"], CallDispatch.INDIRECT_UNRESOLVED)
        self.assertEqual(calls[1]["candidate_target_ids"], [])
        self.assertEqual(semantic["counts"]["resolved_indirect_calls"], 1)
        self.assertEqual(semantic["counts"]["unresolved_indirect_calls"], 1)
        self.assertTrue(
            any(
                relation["kind"] == RelationKind.FUNCTION_POINTER_TARGET
                for relation in semantic["relations"]
            )
        )

    def test_failed_tool_attempt_can_retry_then_extract_all_fact_domains(self) -> None:
        if not shutil.which("clang"):
            self.skipTest("test requires clang")
        with tempfile.TemporaryDirectory() as temporary:
            project, checkouts = ready_project(Path(temporary))
            closure = source_closure(project, checkouts)
            SourceClosureService().validate(
                project,
                closure_path=write_json(project.root / "source-closure.json", closure),
            )

            service = StructuredCAnalysisService()
            failed = service.analyze(project, analyzer="missing-dpf-clang")
            self.assertEqual(failed.status.value, "FAIL")
            self.assertIn("unavailable", failed.errors[0])
            self.assertEqual(
                project.stage(SourceAnalysisStage.STRUCTURED_C_ANALYSIS).status,
                StageStatus.RUNNING,
            )

            passed = service.analyze(project, analyzer="clang")
            self.assertEqual(passed.status.value, "READY")
            self.assertEqual(
                project.stage(SourceAnalysisStage.STRUCTURED_C_ANALYSIS).status,
                StageStatus.PASS,
            )
            self.assertEqual(
                project.stage(MigrationStage.CONTRACTS).status,
                StageStatus.READY,
            )
            facts = project.load_json_artifact(
                SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
                SourceAnalysisArtifact.STRUCTURED_C_FACTS,
            )
            self.assertTrue(
                set(facts["fact_domains"].values())
                <= {"STRUCTURED", "RAW_VALIDATED", "EMPTY_VALID"}
            )
            self.assertEqual(facts["fact_domains"]["typed_ast"], "STRUCTURED")
            self.assertEqual(facts["fact_domains"]["llvm_ir"], "RAW_VALIDATED")
            self.assertTrue(facts["analyzer"]["requested_target_triple"])
            self.assertEqual(len(facts["units"]), 2)
            raw_facts = [
                ref
                for ref in project.artifact_refs(stage=SourceAnalysisStage.STRUCTURED_C_ANALYSIS)
                if ref.kind == SourceAnalysisArtifact.STRUCTURED_C_RAW_FACT.value
            ]
            self.assertGreater(len(raw_facts), 1)
            self.assertGreaterEqual(facts["semantic_counts"]["functions"], 2)
            self.assertGreaterEqual(facts["semantic_counts"]["calls"], 1)
            for unit in facts["units"]:
                self.assertEqual(
                    unit["verified_target_abi"],
                    facts["analyzer"]["target_abi"],
                )
                self.assertEqual(
                    set(unit["raw_facts"]),
                    {
                        "typed_ast",
                        "preprocessed_source",
                        "record_layout",
                        "llvm_ir",
                        "cfg",
                    },
                )
                semantic_path = project.root / unit["semantic_index"]["path"]
                semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
                self.assertGreater(semantic["counts"]["source_spans"], 0)
                self.assertTrue(
                    any(relation["kind"] == "AST_CHILD" for relation in semantic["relations"])
                )
            self.assertTrue(
                any(
                    relation["kind"] == "DIRECT_CALL_TARGET"
                    for unit in facts["units"]
                    for relation in json.loads(
                        (project.root / unit["semantic_index"]["path"]).read_text(encoding="utf-8")
                    )["relations"]
                )
            )
            facts["units"][0]["raw_facts"]["cfg"]["summary"]["block_count"] = 0
            with self.assertRaises(WorkflowError):
                ARTIFACT_VALIDATORS.validate(
                    SourceAnalysisArtifact.STRUCTURED_C_FACTS,
                    (json.dumps(facts) + "\n").encode(),
                )


if __name__ == "__main__":
    unittest.main()
