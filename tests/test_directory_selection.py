"""Real Git selections stay revision-bound without model file enumeration."""

from dataclasses import replace

import pytest

from driver_port_factory.acquisition.directory_selection import expand_directories
from driver_port_factory.acquisition.proposal import (
    EvidenceDiscoveryProposal, load_proposal_occurrence,
)
from driver_port_factory.acquisition.repository import RepositoryAcquirer, load_repository_acquisition
from driver_port_factory.acquisition.repository_role import RepositoryRole as R
from tests.acquisition_support import evidence_proposal, import_evidence_proposal
from tests.repository_support import ready_project


def test_directory_import_uses_frozen_tree_and_preserves_facets(tmp_path):
    project, *_ = ready_project(tmp_path, target_overrides={
        "pci/a.rs": "a", "pci/nested/b.rs": "b", "pci-other/unrelated.rs": "outside",
        "pci/literal[1].rs": "literal", "pci/literal1.rs": "different",
    })
    RepositoryAcquirer().acquire(project)
    selection = evidence_proposal(project)
    template = next(f for f in selection["facets"] if f["lane"] == "source")["locators"][0]
    target = next(f for f in selection["facets"] if f["lane"] == "target")
    target.update(disposition="CONTROLLED", locators=[
        {**template, "repository": "target", "path": path}
        for path in ("pci", "pci/a.rs", "pci/nested")])
    target.pop("gap", None)
    checkout = load_repository_acquisition(project).checkout(R.TARGET)
    root = project.root / checkout.checkout_path
    (root / "pci/untracked.rs").write_text("do not select me")
    (root / "pci/nested/b.rs").unlink()  # Still selected; later retrieval must see the absence.
    imported = import_evidence_proposal(project, selection)
    proposal = load_proposal_occurrence(project, imported.occurrence).proposal
    selected = next(f for f in proposal.facets if f.facet.lane.value == "target")
    assert [l.path for l in selected.locators] == [
        "pci/a.rs", "pci/literal1.rs", "pci/literal[1].rs", "pci/nested/b.rs"]
    assert len(proposal.facets) == len(selection["facets"])
    assert selected.rationale == target["rationale"]
    assert expand_directories(project, proposal) == proposal
    # Exact wildcard-looking names must not expand using Git pathspec magic.
    literal = replace(selected.locators[0], path="pci/literal[1].rs")
    missing = replace(literal, path="pci/missing")
    narrow = replace(selected, locators=(literal, missing))
    result = expand_directories(project, replace(proposal, facets=(narrow,)))
    assert result.facets[0].locators == (literal, missing)
    # Same file in different semantic roles remains in both facets.
    other = replace(proposal.facets[0], locators=(literal,))
    result = expand_directories(project, replace(proposal, facets=(narrow, other)))
    assert result.facets[1].locators == (literal,)
    from driver_port_factory.acquisition.git_material_retrieval import GitMaterialRetriever
    from driver_port_factory.acquisition.retrieval_result import RetrievalFailure
    retriever = GitMaterialRetriever(project.root, load_repository_acquisition(project))
    absent = next(l for l in selected.locators if l.path == "pci/nested/b.rs")
    with pytest.raises(RetrievalFailure, match="absent"):
        retriever.retrieve(selected.facet, absent, "fixture.absent")
    (root / "pci/a.rs").write_text("modified after freeze")
    with pytest.raises(RetrievalFailure, match="differ from Git blob"):
        retriever.retrieve(selected.facet, selected.locators[0], "fixture.changed")


def test_source_and_qemu_directories_expand_under_their_own_revisions(tmp_path):
    project, *_ = ready_project(tmp_path)
    RepositoryAcquirer().acquire(project)
    proposal = EvidenceDiscoveryProposal.from_dict(evidence_proposal(project))
    source = next(f for f in proposal.facets if f.facet.lane.value == "source")
    locator = source.locators[0]
    selected = replace(source, locators=(replace(locator, path="drivers"),
        replace(locator, repository=R.QEMU, path="hw")))
    result = expand_directories(project, replace(proposal, facets=(selected,)))
    assert [(l.repository, l.path) for l in result.facets[0].locators] == [
        (R.SOURCE, "drivers/example.c"), (R.QEMU, "hw/example.c")]
