"""Evidence arrays participate in incremental context without dropping references."""

from driver_port_factory.codex.sessions import input_changes


def test_nested_list_evidence_is_tracked_and_changed_without_losing_content():
    ref = {"kind": "contract", "digest": "old", "path": "/evidence/old"}
    context = {"evidence": [ref, {"nested": [ref]}, "ordinary text"]}
    annotated, known = input_changes(context, {})
    assert annotated["input_changes"]["new"] == ["evidence/0", "evidence/1/nested/0"]
    assert annotated["evidence"] == context["evidence"]
    repeat, _ = input_changes(context, known)
    assert repeat["input_changes"]["unchanged"] == ["evidence/0", "evidence/1/nested/0"]
    changed = {"evidence": [{**ref, "digest": "new", "path": "/evidence/new"}]}
    annotated, _ = input_changes(changed, known)
    assert annotated["input_changes"]["changed"] == ["evidence/0"]


def test_list_reordering_does_not_claim_unchanged_at_new_positions():
    a = {"kind": "contract", "digest": "a", "path": "/a"}
    b = {"kind": "contract", "digest": "b", "path": "/b"}
    _, known = input_changes({"evidence": [a, b]}, {})
    updated, _ = input_changes({"evidence": [b, a]}, known)
    assert updated["input_changes"]["changed"] == ["evidence/0", "evidence/1"]
