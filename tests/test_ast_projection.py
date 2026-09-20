from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
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
    @unittest.skipUnless(shutil.which("clang"), "requires Clang")
    def test_compiler_dependency_closure_keeps_shared_types_and_callback_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "include").mkdir()
            header = root / "include/shared.h"
            local = root / "driver.h"
            local.write_text("static inline int exported_helper(void) { return 1; }\n")
            source = root / "driver.c"
            header.write_text('''
typedef unsigned long count_t;
struct shared { count_t count; int (*run)(int); };
static inline int leaf(int n) { return n + 1; }
static inline int callback(int);
static inline int callback(int n) { return leaf(n); }
static inline int unused(int n) { return n - 1; }
struct unrelated { char waste[1000]; };
''')
            source.write_text('''#include "include/shared.h"
#include "driver.h"
static const struct shared device = { 1, callback };
int probe(void) { return device.run(device.count); }
''')
            capture = root / "capture.json"
            with capture.open("wb") as stream:
                subprocess.run(["clang", "-Xclang", "-ast-dump=json", "-fsyntax-only",
                                str(source)], stdout=stream, check=True)
            closure = ClosureFileSet(root, tuple(
                ClosureFile(path.relative_to(root).as_posix(), path,
                            hashlib.sha256(path.read_bytes()).hexdigest())
                for path in (source, header, local)
            ))
            projector = ClosureAstProjector(closure)
            provenance = dict(compile_directory=root,
                              capture_sha256=hashlib.sha256(capture.read_bytes()).hexdigest(),
                              capture_size=capture.stat().st_size)
            ast = projector.project(capture, root / "facts.json", **provenance)
            projector.validate(ast, **provenance)
            names = {node.get("name") for node in ast["inner"]}
            self.assertTrue({"probe", "device", "shared", "count_t", "callback", "leaf",
                             "exported_helper"} <= names)
            self.assertTrue({"unused", "unrelated"}.isdisjoint(names))
            semantic = AstSemanticIndexer("test", source, ast).build()
            self.assertEqual(semantic["counts"]["function_definitions"], 4)
            self.assertEqual(semantic["counts"]["resolved_indirect_calls"], 1)

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
