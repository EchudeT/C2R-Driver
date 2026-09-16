from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from driver_port_factory.source_analysis.ast_index import AstSemanticIndexer
from driver_port_factory.source_analysis.ast_projection import (
    ClosureAstProjector,
    ClosureFile,
    ClosureFileSet,
)


class ClosureAstProjectionTests(unittest.TestCase):
    def test_descendant_macro_location_does_not_own_external_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "driver.c"
            source.write_text("int probe(void);\n", encoding="utf-8")
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            external = {
                "id": "external",
                "kind": "FunctionDecl",
                "name": "framework_inline",
                "loc": {"file": "/sdk/framework.h", "line": 1, "col": 1},
                "inner": [
                    {
                        "id": "attribute",
                        "kind": "NoInstrumentFunctionAttr",
                        "loc": {
                            "spellingLoc": {
                                "file": str(source),
                                "line": 1,
                                "col": 1,
                            }
                        },
                    }
                ],
            }
            owned = {
                "id": "probe",
                "kind": "FunctionDecl",
                "name": "probe",
                "loc": {"file": str(source), "line": 1, "col": 1},
            }
            capture = root / "ast.json"
            capture.write_text(
                json.dumps({"kind": "TranslationUnitDecl", "inner": [external, owned]}),
                encoding="utf-8",
            )
            projection = ClosureAstProjector(
                ClosureFileSet(root, (ClosureFile("driver.c", source, source_digest),))
            ).project(
                capture,
                root / "closure-ast.json",
                compile_directory=root,
                capture_sha256=hashlib.sha256(capture.read_bytes()).hexdigest(),
                capture_size=capture.stat().st_size,
            )

            self.assertEqual([node["name"] for node in projection["inner"]], ["probe"])

    def test_excludes_unrelated_headers_and_keeps_external_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "driver.c"
            source.write_text("int probe(void);\n", encoding="utf-8")
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            external = [
                {
                    "id": f"external-{index}",
                    "kind": "FunctionDecl",
                    "name": f"framework_{index}",
                    "loc": {"file": "/sdk/framework.h", "line": index + 1, "col": 1},
                    "type": {"qualType": "int (void)"},
                }
                for index in range(4_000)
            ]
            external_target = external[0]
            owned = {
                "id": "probe",
                "kind": "FunctionDecl",
                "name": "probe",
                "loc": {"file": str(source), "line": 1, "col": 1},
                "type": {"qualType": "int (void)"},
                "inner": [
                    {
                        "id": "body",
                        "kind": "CompoundStmt",
                        "inner": [
                            {
                                "id": "call",
                                "kind": "CallExpr",
                                "inner": [
                                    {
                                        "id": "callee",
                                        "kind": "DeclRefExpr",
                                        "referencedDecl": external_target,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
            capture = root / "ast.json"
            capture.write_text(
                json.dumps({"kind": "TranslationUnitDecl", "inner": [*external, owned]}),
                encoding="utf-8",
            )
            capture_digest = hashlib.sha256(capture.read_bytes()).hexdigest()
            projection_path = root / "closure-ast.json"
            closure = ClosureFileSet(
                root,
                (ClosureFile("driver.c", source, source_digest),),
            )

            projection = ClosureAstProjector(closure).project(
                capture,
                projection_path,
                compile_directory=root,
                capture_sha256=capture_digest,
                capture_size=capture.stat().st_size,
            )
            semantic = AstSemanticIndexer("fixture", source, projection).build()

            self.assertEqual(len(projection["inner"]), 1)
            self.assertEqual(projection["dpfClosure"]["observed_top_level_nodes"], 4_001)
            self.assertLess(projection_path.stat().st_size, capture.stat().st_size // 20)
            self.assertEqual(semantic["counts"]["function_definitions"], 1)
            self.assertEqual(semantic["counts"]["external_declarations"], 1)
            self.assertEqual(len(semantic["indexes"]["external_declarations"]), 1)


if __name__ == "__main__":
    unittest.main()
