from __future__ import annotations

import unittest
from pathlib import Path

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.source_analysis.ast_index import AstSemanticIndexer
from driver_port_factory.source_analysis.function_pointers import (
    ClosureFunctionPointerResolver,
)
from driver_port_factory.source_analysis.semantic_model import CallDispatch, RelationKind
from driver_port_factory.source_analysis.semantic_validation import validate_semantic_index


def field(identifier: str) -> dict[str, object]:
    return {
        "id": identifier,
        "kind": "FieldDecl",
        "name": "callback",
        "loc": {"file": "shared.h", "line": 2, "col": 10},
        "type": {"qualType": "int (*)(int)"},
    }


def unit_a(*, exact: bool) -> dict[str, object]:
    target = {
        "id": "target-a",
        "kind": "FunctionDecl",
        "name": "implementation",
        "loc": {"file": "a.c", "line": 3, "col": 1},
        "type": {"qualType": "int (int)"},
        "inner": [{"id": "target-body", "kind": "CompoundStmt"}],
    }
    rhs = (
        {
            "id": "decay",
            "kind": "ImplicitCastExpr",
            "castKind": "FunctionToPointerDecay",
            "type": {"qualType": "int (*)(int)"},
            "inner": [
                {
                    "id": "target-ref",
                    "kind": "DeclRefExpr",
                    "referencedDecl": {
                        "id": "target-a",
                        "kind": "FunctionDecl",
                        "name": "implementation",
                        "type": {"qualType": "int (int)"},
                    },
                }
            ],
        }
        if exact
        else {"id": "unknown", "kind": "DeclRefExpr", "type": {"qualType": "int (*)(int)"}}
    )
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "id": "record-a",
                "kind": "RecordDecl",
                "name": "operations",
                "tagUsed": "struct",
                "loc": {"file": "shared.h", "line": 1, "col": 1},
                "inner": [field("field-a")],
            },
            target,
            {
                "id": "configure",
                "kind": "FunctionDecl",
                "name": "configure",
                "loc": {"file": "a.c", "line": 4, "col": 1},
                "type": {"qualType": "void (struct operations *)"},
                "inner": [
                    {
                        "id": "configure-body",
                        "kind": "CompoundStmt",
                        "inner": [
                            {
                                "id": "assignment",
                                "kind": "BinaryOperator",
                                "opcode": "=",
                                "inner": [
                                    {
                                        "id": "lhs",
                                        "kind": "MemberExpr",
                                        "isArrow": True,
                                        "referencedMemberDecl": "field-a",
                                        "type": {"qualType": "int (*)(int)"},
                                    },
                                    rhs,
                                ],
                            }
                        ],
                    }
                ],
            },
        ],
    }


def unit_b() -> dict[str, object]:
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "id": "record-b",
                "kind": "RecordDecl",
                "name": "operations",
                "tagUsed": "struct",
                "loc": {"file": "shared.h", "line": 1, "col": 1},
                "inner": [field("field-b")],
            },
            {
                "id": "invoke",
                "kind": "FunctionDecl",
                "name": "invoke",
                "loc": {"file": "b.c", "line": 3, "col": 1},
                "type": {"qualType": "int (struct operations *, int)"},
                "inner": [
                    {
                        "id": "invoke-body",
                        "kind": "CompoundStmt",
                        "inner": [
                            {
                                "id": "call",
                                "kind": "CallExpr",
                                "inner": [
                                    {
                                        "id": "callee",
                                        "kind": "MemberExpr",
                                        "isArrow": True,
                                        "referencedMemberDecl": "field-b",
                                        "type": {"qualType": "int (*)(int)"},
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        ],
    }


class ClosureFunctionPointerTests(unittest.TestCase):
    def test_cross_unit_field_assignment_resolves_exactly_and_unknown_write_fails(self) -> None:
        writer = AstSemanticIndexer("writer", Path("a.c"), unit_a(exact=True)).build()
        caller = AstSemanticIndexer("caller", Path("b.c"), unit_b()).build()

        ClosureFunctionPointerResolver.resolve([writer, caller])

        call = caller["indexes"]["calls"][0]
        target = writer["indexes"]["definition_identities"]["functions"][0]["node_id"]
        self.assertEqual(call["dispatch"], CallDispatch.INDIRECT_RESOLVED)
        self.assertEqual(call["candidate_target_ids"], [target])
        self.assertIn(
            {
                "kind": RelationKind.INDIRECT_CALL_TARGET,
                "source": call["node_id"],
                "target": target,
            },
            caller["relations"],
        )
        self.assertEqual(caller["indexes"]["closure_targets"][0]["unit_id"], "writer")
        validate_semantic_index(writer)
        validate_semantic_index(caller)

        unknown_writer = AstSemanticIndexer(
            "unknown-writer", Path("a.c"), unit_a(exact=False)
        ).build()
        unknown_caller = AstSemanticIndexer("unknown-caller", Path("b.c"), unit_b()).build()
        with self.assertRaisesRegex(WorkflowError, "remain unresolved across source closure"):
            ClosureFunctionPointerResolver.resolve([unknown_writer, unknown_caller])


if __name__ == "__main__":
    unittest.main()
