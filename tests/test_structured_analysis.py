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
from driver_port_factory.source_analysis.compiler import AbiCompatibility, GccCompatibleCommand
from driver_port_factory.source_analysis.contracts import (
    SourceAnalysisArtifact,
    SourceAnalysisStage,
)
from driver_port_factory.source_analysis.fact_model import RawFactKind
from driver_port_factory.source_analysis.fact_parsers import RawFactParser
from driver_port_factory.source_analysis.semantic_model import CallDispatch, RelationKind
from driver_port_factory.source_analysis.structured import StructuredCAnalysisService
from tests.test_source_closure import ready_project, source_closure, write_json


class StructuredCAnalysisTests(unittest.TestCase):
    def test_cfg_known_clang_omission_is_explicit_but_unknown_missing_body_fails(self):
        parser = RawFactParser()
        identity = {"node_id": "inline-id", "name": "__inline_memcpy"}
        unavailable = parser._correlate_functions([], [identity])
        self.assertEqual(unavailable[0]["ast_node_id"], "inline-id")
        self.assertEqual(unavailable[0]["reason"], "clang-analyzer-skips-__inline-prefix")
        with self.assertRaisesRegex(WorkflowError, "does not cover"):
            parser._correlate_functions([], [{**identity, "name": "driver_probe"}])

    def test_external_declarations_exclude_unreferenced_analysis_candidates(self) -> None:
        ast = {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "id": "table",
                    "kind": "VarDecl",
                    "loc": {"file": "driver.c", "line": 1, "col": 1},
                    "type": {"qualType": "const struct operations"},
                    "inner": [
                        {
                            "id": "initializer",
                            "kind": "InitListExpr",
                            "field": {
                                "id": "external-field",
                                "kind": "FieldDecl",
                                "name": "callback",
                                "type": {"qualType": "int (*)(void)"},
                            },
                            "inner": [
                                {
                                    "id": "zero",
                                    "kind": "IntegerLiteral",
                                    "type": {"qualType": "int"},
                                    "value": "0",
                                }
                            ],
                        }
                    ],
                }
            ],
        }

        indexer = AstSemanticIndexer("external-candidate", Path("driver.c"), ast)
        semantic = indexer.build()

        self.assertEqual(len(indexer.external_declarations), 1)
        self.assertEqual(semantic["indexes"]["external_declarations"], [])
        self.assertEqual(semantic["counts"]["external_declarations"], 0)

    def test_anonymous_record_inherits_elided_clang_source_location(self) -> None:
        header = "/sdk/include/driver.h"
        source = Path("driver.c")
        record = {
            "id": "anonymous-record",
            "kind": "RecordDecl",
            "tagUsed": "struct",
            "loc": {"offset": 4605, "col": 11},
            "inner": [
                {
                    "id": "field",
                    "kind": "FieldDecl",
                    "name": "value",
                    "type": {"qualType": "int"},
                }
            ],
        }
        ast = {
            "id": "translation-unit",
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "id": "parent",
                    "kind": "LinkageSpecDecl",
                    "loc": {"file": header, "line": 198, "col": 1},
                    "range": {
                        "begin": {"line": 199, "col": 1},
                        "end": {"col": 40},
                    },
                    "inner": [record],
                }
            ],
        }

        semantic = AstSemanticIndexer("elided-locations", source, ast).build()

        identity = semantic["indexes"]["definition_identities"]["records"][0]
        self.assertEqual(
            identity["source_location"],
            {"file": header, "line": 199, "col": 11},
        )
        self.assertEqual(identity["source_location_candidates"][0]["kind"], "direct")
        self.assertIn(
            f"struct (unnamed at {header}:199:11)",
            identity["layout_labels"],
        )
        layout = (
            "*** Dumping AST Record Layout\n"
            f" 0 | struct (unnamed at {header}:199:11)\n"
            " 0 |   int value\n"
            "   | [sizeof=4, align=4]\n"
        ).encode()
        _, summary = RawFactParser().summarize(
            RawFactKind.RECORD_LAYOUT,
            layout,
            semantic,
            "fixture-target",
        )
        self.assertEqual(summary["records"][0]["ast_node_id"], identity["node_id"])

    def test_macro_generated_record_layout_can_use_expansion_location(self) -> None:
        spelling_file = "/sdk/include/macro-definition.h"
        expansion_file = "/sdk/include/macro-use.h"
        record = {
            "id": "macro-record",
            "kind": "RecordDecl",
            "tagUsed": "struct",
            "loc": {
                "spellingLoc": {
                    "offset": 100,
                    "file": spelling_file,
                    "line": 40,
                    "col": 9,
                },
                "expansionLoc": {
                    "offset": 200,
                    "file": expansion_file,
                    "line": 70,
                    "col": 1,
                },
            },
            "inner": [
                {
                    "id": "field",
                    "kind": "FieldDecl",
                    "name": "value",
                    "type": {"qualType": "int"},
                }
            ],
        }
        ast = {
            "id": "translation-unit",
            "kind": "TranslationUnitDecl",
            "inner": [record],
        }

        semantic = AstSemanticIndexer("macro-location", Path("driver.c"), ast).build()

        identity = semantic["indexes"]["definition_identities"]["records"][0]
        self.assertEqual(
            identity["source_location"],
            {"file": spelling_file, "line": 40, "col": 9},
        )
        self.assertEqual(
            [candidate["kind"] for candidate in identity["source_location_candidates"]],
            ["spelling", "expansion"],
        )
        layout = (
            "*** Dumping AST Record Layout\n"
            f" 0 | struct (unnamed at {expansion_file}:70:1)\n"
            " 0 |   int value\n"
            "   | [sizeof=4, align=4]\n"
        ).encode()
        _, summary = RawFactParser().summarize(
            RawFactKind.RECORD_LAYOUT,
            layout,
            semantic,
            "fixture-target",
        )
        self.assertEqual(summary["records"][0]["ast_node_id"], identity["node_id"])

    def test_record_layout_uses_direct_fields_to_disambiguate_shared_location(self) -> None:
        source_file = "/sdk/include/types.h"
        record = {
            "id": "populated-record",
            "kind": "RecordDecl",
            "tagUsed": "struct",
            "loc": {"file": source_file, "line": 25, "col": 3},
            "inner": [
                {
                    "id": "first-field",
                    "kind": "FieldDecl",
                    "name": "first_member",
                    "type": {"qualType": "struct wrapper"},
                },
                {
                    "id": "second-field",
                    "kind": "FieldDecl",
                    "name": "payload",
                    "type": {"qualType": "char[]"},
                },
            ],
        }
        ast = {
            "id": "translation-unit",
            "kind": "TranslationUnitDecl",
            "inner": [record],
        }
        semantic = AstSemanticIndexer("record-structure", Path("driver.c"), ast).build()
        label = f"struct (unnamed at {source_file}:25:3)"
        layout = (
            "*** Dumping AST Record Layout\n"
            f" 0 | {label}\n"
            "   | [sizeof=0, align=1]\n"
            "*** Dumping AST Record Layout\n"
            f" 0 | {label}\n"
            " 0 |   struct wrapper first_member\n"
            " 0 |     unsigned int nested_member\n"
            " 0 |   char[] payload\n"
            "   | [sizeof=4, align=4]\n"
            "*** Dumping AST Record Layout\n"
            f" 0 | {label}\n"
            " 0 |   struct wrapper first_member\n"
            " 0 |     unsigned int nested_member\n"
            " 0 |   char[] payload\n"
            "   | [sizeof=4, align=4]\n"
        ).encode()

        _, summary = RawFactParser().summarize(
            RawFactKind.RECORD_LAYOUT,
            layout,
            semantic,
            "fixture-target",
        )

        identity = semantic["indexes"]["definition_identities"]["records"][0]
        self.assertEqual(summary["records"][0]["ast_node_id"], identity["node_id"])
        self.assertEqual(
            [field["depth"] for field in summary["records"][0]["fields"]],
            [1, 2, 1],
        )
        self.assertEqual(summary["record_fact_count"], 1)

    def test_record_layout_keeps_facts_with_different_field_offsets(self) -> None:
        label = "struct example"
        first = (
            "*** Dumping AST Record Layout\n"
            f" 0 | {label}\n"
            " 0 |   int value\n"
            "   | [sizeof=4, align=4]\n"
        )
        duplicate = first + first
        different = (
            "*** Dumping AST Record Layout\n"
            f" 0 | {label}\n"
            " 4 |   int value\n"
            "   | [sizeof=4, align=4]\n"
        )

        parser = RawFactParser()
        self.assertEqual(len(parser._parse_record_layout(duplicate.splitlines())), 2)
        self.assertEqual(len(parser._parse_record_layout((duplicate + different).splitlines())), 3)

    def test_macro_duplicate_layouts_bind_distinct_ast_definitions(self) -> None:
        compiler = shutil.which("clang-18") or shutil.which("clang")
        if compiler is None:
            self.skipTest("Clang is required")
        source = (
            "#define GROUP(name, ...) union { struct { __VA_ARGS__ }; "
            "struct { __VA_ARGS__ } name; }\n"
            "struct packet { GROUP(headers, unsigned short start; "
            "unsigned short offset;); };\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.c"
            path.write_text(source)
            ast = subprocess.run(
                [compiler, "-Xclang", "-ast-dump=json", "-fsyntax-only", str(path)],
                check=True, capture_output=True,
            )
            layout = subprocess.run(
                [compiler, "-Xclang", "-fdump-record-layouts-complete",
                 "-fsyntax-only", str(path)],
                check=True, capture_output=True,
            )
            semantic = AstSemanticIndexer("macro", path, json.loads(ast.stdout)).build()
            parser = RawFactParser()
            _, summary = parser.summarize(
                RawFactKind.RECORD_LAYOUT, layout.stdout, semantic, "fixture-target"
            )
            identities = semantic["indexes"]["definition_identities"]["records"]
            self.assertEqual(
                {record["ast_node_id"] for record in summary["records"]},
                {identity["node_id"] for identity in identities},
            )
            self.assertGreaterEqual(len(identities), 4)
            # An identical second occurrence is evidence, not a duplicate to discard.
            records = parser._parse_record_layout(layout.stdout.decode().splitlines())
            repeated = next(record for record in records if records.count(record) > 1)
            records.remove(repeated)
            with self.assertRaises(WorkflowError):
                parser._correlate_records(records, identities)

    def test_abi_compatibility_ignores_compiler_specific_macro_spelling(self) -> None:
        expected = {
            "target_triple": "x86_64-linux-gnu",
            "pointer_width_bits": 64,
            "long_width_bits": 64,
            "long_long_width_bits": 64,
            "int_width_bits": 32,
            "size_t_width_bits": 64,
            "char_width_bits": 8,
            "byte_order": "little",
            "wchar_width_bits": 32,
            "biggest_alignment_bytes": 16,
            "abi_flags": ["-m64"],
            "predefined_macros": {"__BYTE_ORDER__": "__ORDER_LITTLE_ENDIAN__"},
            "fingerprint_sha256": "0" * 64,
        }
        observed = {
            **expected,
            "target_triple": "x86_64-unknown-linux-gnu",
            "predefined_macros": {"__BYTE_ORDER__": "1234"},
            "fingerprint_sha256": "f" * 64,
        }

        self.assertEqual(
            AbiCompatibility.from_record(expected),
            AbiCompatibility.from_record(observed),
        )

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

    def test_clang_analyzes_frozen_gcc_compile_commands(self) -> None:
        gcc = shutil.which("gcc")
        clang = shutil.which("clang")
        if not gcc or not clang:
            self.skipTest("test requires GCC and Clang")
        with tempfile.TemporaryDirectory() as temporary:
            project, checkouts = ready_project(Path(temporary))
            closure = source_closure(project, checkouts)
            source_root = Path(closure["source_root"])
            for unit in closure["translation_units"]:
                unit["arguments"][0] = gcc
            probe_arguments = closure["translation_units"][0]["arguments"]
            version = GccCompatibleCommand.version(Path(gcc).absolute())
            closure["compiler"] = {
                "family": "gcc-compatible",
                "executable": gcc,
                "version": version.splitlines()[0],
                "target_triple": GccCompatibleCommand.effective_target_triple(
                    probe_arguments, source_root
                ),
                "target_abi": GccCompatibleCommand.abi_signature(
                    probe_arguments, source_root
                ),
                "language_mode": "gnu11",
            }
            SourceClosureService().validate(
                project,
                closure_path=write_json(project.root / "source-closure.json", closure),
            )

            result = StructuredCAnalysisService().analyze(project, analyzer=clang)

            self.assertEqual(result.stage_status, StageStatus.PASS, result.errors)
            facts = project.load_json_artifact(
                SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
                SourceAnalysisArtifact.STRUCTURED_C_FACTS,
            )
            compiler = project.load_json_artifact(
                SourceAnalysisStage.SOURCE_CLOSURE,
                SourceAnalysisArtifact.COMPILE_MANIFEST,
            )["compiler"]
            self.assertNotEqual(facts["analyzer"]["sha256"], compiler["sha256"])
            self.assertEqual(
                AbiCompatibility.from_record(facts["analyzer"]["target_abi"]),
                AbiCompatibility.from_record(compiler["verified_target_abi"]),
            )


if __name__ == "__main__":
    unittest.main()
