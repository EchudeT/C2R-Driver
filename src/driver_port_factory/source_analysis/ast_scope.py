"""Reachability over compiler declarations, never over C source spelling."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


class DeclarationScope:
    """Keep source roots and their transitive declaration/type dependencies.

    Clang's JSON has declaration IDs for expressions, but some QualTypes only
    have compiler-rendered spelling. Match complete type identifiers against
    declarations in that SAME translation unit, retaining all ambiguous matches.
    This is identity correlation, not an inference about C behavior.
    """

    def __init__(self) -> None:
        self.owners: dict[str, set[int]] = defaultdict(set)
        self.types: dict[str, set[int]] = defaultdict(set)
        self.names: dict[str, set[int]] = defaultdict(set)
        self.name_uses: list[set[str]] = []
        self.references: list[set[str]] = []
        self.type_uses: list[set[str]] = []
        self.roots: set[int] = set()

    def add(self, node: dict[str, Any], *, root: bool) -> None:  # noqa: C901
        index = len(self.references)
        refs: set[str] = set()
        types: set[str] = set()
        names: set[str] = set()
        self.name_uses.append(names)
        self.references.append(refs)
        self.type_uses.append(types)
        if root:
            self.roots.add(index)

        def scan(value: Any, *, declaration: bool = False) -> None:  # noqa: C901
            if isinstance(value, list):
                for child in value:
                    scan(child, declaration=declaration)
                return
            if not isinstance(value, dict):
                return
            kind = value.get("kind", "")
            identifier = value.get("id")
            if isinstance(identifier, str):
                refs.add(identifier)
                if declaration:
                    self.owners[identifier].add(index)
            if declaration and kind in {"RecordDecl", "EnumDecl", "TypedefDecl"}:
                name = value.get("name")
                if name:
                    self.types[name].add(index)
            if declaration and kind.endswith("Decl") and isinstance(value.get("name"), str):
                self.names[value["name"]].add(index)
            for key, child in value.items():
                if key in {"qualType", "desugaredQualType"} and isinstance(child, str):
                    types.update(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", child))
                elif isinstance(child, str) and key in {"aliasee", "resolver"}:
                    # AliasAttr / IFuncAttr encode dependencies as names, not IDs.
                    names.add(child)
                elif isinstance(child, str) and key in {
                    "previousDecl", "referencedMemberDecl", "typeAliasDeclId",
                    "ownedTagDecl", "targetLabelDeclId",
                }:
                    refs.add(child)
                    if key == "previousDecl" and declaration:
                        # A call may point at an earlier prototype. Follow its
                        # redeclarations to the definition as well as backwards.
                        self.owners[child].add(index)
                elif key not in {"loc", "range"}:
                    scan(child, declaration=key == "inner")

        scan(node, declaration=True)

    def selected(self) -> set[int]:
        selected = set(self.roots)
        pending = list(selected)
        while pending:
            current = pending.pop()
            dependencies: set[int] = set()
            for identifier in self.references[current]:
                dependencies.update(self.owners.get(identifier, ()))
            for name in self.type_uses[current]:
                dependencies.update(self.types.get(name, ()))
            for name in self.name_uses[current]:
                dependencies.update(self.names.get(name, ()))
            for dependency in dependencies - selected:
                selected.add(dependency)
                pending.append(dependency)
        return selected
