"""Syntax-based Rust change impact, not a substitute for compiler/type safety checks.

Index both sides of a change. Name collisions deliberately over-approximate edges;
macros and dynamic dispatch are not claimed to be fully resolved.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from tree_sitter import Language, Parser
import tree_sitter_rust

COMMENTS = {"line_comment", "block_comment"}
LITERALS = {"string_literal", "raw_string_literal", "char_literal"}
DECLARATIONS = {"function_item", "function_signature_item", "struct_item", "enum_item",
                "trait_item", "type_item", "const_item", "static_item", "macro_definition",
                "field_declaration", "enum_variant", "mod_item"}


class RiskAnalysisLimit(Exception):
    """The cheap syntax index cannot resolve this scope within its fixed budget."""


@dataclass
class Item:
    path: str
    scope: str
    line: int
    tokens: tuple
    names: set[str]
    references: set[str]
    unsafe: bool
    uncertain: bool


def _walk(node):
    yield node
    for child in node.children:
        if child.type not in COMMENTS and child.type not in LITERALS:
            yield from _walk(child)


def _tokens(node):
    if node.type in COMMENTS:
        return
    if not node.children or node.type in LITERALS:
        yield (node.type, node.text)
    else:
        for child in node.children:
            yield from _tokens(child)


def parse_items(path: str, source: bytes) -> list[Item]:
    root = Parser(Language(tree_sitter_rust.language())).parse(source).root_node
    result = []

    def collect(container, prefix="", inherited=()):
        attributes = []
        for node in container.named_children:
            if node.type in COMMENTS:
                continue
            if node.type == "inner_attribute_item":
                inherited = (*inherited, *_tokens(node))
                continue
            if node.type == "attribute_item":
                attributes.extend(_tokens(node))
                continue
            context = (*inherited, *attributes)
            attributes.clear()
            name_node = node.child_by_field_name("name")
            name = name_node.text.decode() if name_node else node.type
            if node.type == "impl_item":
                target_type = node.child_by_field_name("type")
                name = "impl " + (target_type.text.decode() if target_type else "unresolved")
            scope = prefix + name
            body = node.child_by_field_name("body")
            if node.type == "mod_item" and body is not None:
                header = tuple(token for child in node.children if child != body
                               for token in _tokens(child))
                collect(body, scope + "::", (*context, *header))
                continue
            nodes = list(_walk(node))
            names = {child.text.decode() for declaration in nodes
                     if declaration.type in DECLARATIONS
                     if (child := declaration.child_by_field_name("name")) is not None}
            references = {child.text.decode() for child in nodes
                          if child.type in {"identifier", "type_identifier", "field_identifier"}}
            if node.type == "use_declaration":
                # Connect aliases to their imported names without guessing one resolution.
                names |= references
            tokens = (*context, *_tokens(node))
            result.append(Item(path, scope, node.start_point.row + 1, tokens, names,
                               references, any(kind == "unsafe" for kind, _ in tokens)
                               or node.type == "foreign_mod_item", node.has_error
                               or any(child.type in {"macro_invocation", "use_wildcard"}
                                      for child in nodes)))
        if attributes:
            result.append(Item(path, prefix + "attributes", 1, tuple(attributes), set(),
                               set(), False, True))

    collect(root)
    return result


def _reachable(seeds, edges):
    """Record one concrete shortest dependency witness, not a huge graph in the prompt."""
    witnesses = {seed: seed for seed in seeds}
    queue = deque(sorted(seeds))
    while queue:
        current = queue.popleft()
        for adjacent in sorted(edges.get(current, ())):
            if adjacent not in witnesses:
                witnesses[adjacent] = witnesses[current]
                queue.append(adjacent)
    return witnesses


def impacted_risks(before: dict[str, bytes], after: dict[str, bytes]) -> list[dict]:
    versions = []
    cache = {}
    for sources in (before, after):
        items = {}
        for path, source in sorted(sources.items()):
            cached = cache.get(path)
            if cached is not None and cached[0] == source:
                items.update(cached[1])
                continue
            parsed = {}
            for item in parse_items(path, source):
                key = (path, item.scope)
                if key in parsed:
                    # cfg alternatives / multiple impl blocks: merge conservatively.
                    old = parsed[key]
                    item.tokens = (*old.tokens, *item.tokens)
                    item.names |= old.names
                    item.references |= old.references
                    item.unsafe |= old.unsafe
                    item.uncertain |= old.uncertain
                parsed[key] = item
            cache[path] = (source, parsed)
            items.update(parsed)
        versions.append(items)
    old, new = versions
    changed = {key for key in old.keys() | new.keys()
               if key not in old or key not in new or old[key].tokens != new[key].tokens}
    if not changed:
        return []
    reasons = {}
    for items in versions:
        definitions = defaultdict(set)
        for key, item in items.items():
            for name in item.names:
                definitions[name].add(key)
        forward, reverse = defaultdict(set), defaultdict(set)
        edge_count = 0
        # A shared name vertex avoids a quadratic cross-product for methods like new().
        for name, targets in definitions.items():
            symbol = (name,)
            forward[symbol].update(targets)
            for target in targets:
                reverse[target].add(symbol)
            edge_count += len(targets)
        for key, item in items.items():
            for name in item.references:
                if name not in definitions:
                    continue
                symbol = (name,)
                forward[key].add(symbol)
                reverse[symbol].add(key)
                edge_count += 1
                if edge_count > 500_000:
                    raise RiskAnalysisLimit("Rust name index exceeds 500000 dependency edges")
        seeds = {key for key, item in items.items() if item.unsafe}
        dependencies = _reachable(seeds, forward)
        callers = _reachable(seeds, reverse)
        for key in sorted(changed & items.keys()):
            item = items[key]
            witness = dependencies.get(key, callers.get(key))
            uncertain = item.uncertain
            if witness is None and not uncertain:
                continue
            boundary = items[witness] if witness is not None else item
            reasons[key] = {
                "kind": "unresolved_rust_impact" if uncertain else "unsafe_boundary",
                "path": item.path, "scope": item.scope,
                "line": item.line,
                "basis": "unresolved_syntax_or_macro" if uncertain else
                         "changed_boundary" if item.unsafe else "name_dependency",
                "boundary": {"path": boundary.path, "scope": boundary.scope,
                             "line": boundary.line},
            }
    return [reasons[key] for key in sorted(reasons)]
