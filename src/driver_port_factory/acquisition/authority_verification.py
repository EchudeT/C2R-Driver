from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from functools import singledispatchmethod
from pathlib import Path

from ..core.execution import CommandRunner
from .authority import (
    AuthorityBasis,
    CorroboratedAuthority,
    EvidenceAuthority,
    RepositoryEndorsementAuthority,
    OriginalPublisherAuthority,
)
from .facets import RetrievalOutcome
from .http_content import EvidenceHttpContentRecorder, RecordedHttpContent
from .locators import ExternalUrlLocator
from .material import EvidenceContentRef, HttpResponseRecord
from .repository_manifest import RepositoryAcquisition
from .retrieval_result import RetrievalFailure


@dataclass(frozen=True, slots=True)
class AuthorityVerification:
    authority: EvidenceAuthority
    corroboration: tuple[HttpResponseRecord, ...]
    content_refs: tuple[EvidenceContentRef, ...]


@dataclass(frozen=True, slots=True)
class PublisherIdentity:
    hostname: str

    @classmethod
    def from_url(cls, value: str) -> PublisherIdentity:
        parsed = urllib.parse.urlparse(value)
        if parsed.hostname:
            return cls(parsed.hostname.casefold())
        raise RetrievalFailure(
            RetrievalOutcome.CONFLICT,
            "corroboration URL has no network publisher identity",
        )


class RepositoryEndorsementVerifier:
    def __init__(self, project_root: Path, acquisition: RepositoryAcquisition) -> None:
        self.root = project_root.resolve()
        self.acquisition = acquisition
        self.git_runs = CommandRunner(self.root / ".dpf" / "command-runs" / "evidence-closure")

    def verify(
        self,
        authority: RepositoryEndorsementAuthority,
        locator: ExternalUrlLocator,
        primary: RecordedHttpContent,
    ) -> AuthorityVerification:
        checkout = self.acquisition.checkout(authority.repository)
        checkout_root = (self.root / checkout.checkout_path).resolve()
        blob = (
            self._git(
                checkout_root,
                ("rev-parse", f"{checkout.resolved_commit}:{authority.path}"),
            )
            .decode("ascii", errors="strict")
            .strip()
        )
        content = self._git(checkout_root, ("cat-file", "blob", blob))
        try:
            lines = content.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise RetrievalFailure(
                RetrievalOutcome.CONFLICT,
                "repository endorsement source is not UTF-8 text",
                (primary.response.content_ref,),
            ) from error
        if authority.line_end > len(lines):
            raise RetrievalFailure(
                RetrievalOutcome.CONFLICT,
                "repository endorsement span exceeds its frozen Git blob",
                (primary.response.content_ref,),
            )
        excerpt = "\n".join(lines[authority.line_start - 1 : authority.line_end])
        if locator.source_url not in excerpt and primary.response.resolved_url not in excerpt:
            raise RetrievalFailure(
                RetrievalOutcome.CONFLICT,
                "frozen repository endorsement does not reference the external URL",
                (primary.response.content_ref,),
            )
        return AuthorityVerification(
            EvidenceAuthority.PRIMARY,
            (),
            (primary.response.content_ref,),
        )

    def _git(self, cwd: Path, arguments: tuple[str, ...]) -> bytes:
        result = self.git_runs.run(("git", "-C", str(cwd), *arguments), cwd=self.root)
        stdout = Path(result.stdout_path).read_bytes()
        if result.exit_code != 0:
            stderr = Path(result.stderr_path).read_text(encoding="utf-8", errors="replace")
            raise RetrievalFailure(
                RetrievalOutcome.CONFLICT,
                stderr.strip() or "Git endorsement lookup failed",
            )
        return stdout


class CorroboratedAuthorityVerifier:
    def __init__(self, content: EvidenceHttpContentRecorder) -> None:
        self.content = content

    def verify(
        self,
        authority: CorroboratedAuthority,
        locator: ExternalUrlLocator,
        primary: RecordedHttpContent,
    ) -> AuthorityVerification:
        corroboration: list[HttpResponseRecord] = []
        recorded_refs = [primary.response.content_ref]
        for source in authority.sources:
            try:
                retrieved = self.content.retrieve(
                    source.source_url,
                    expected_sha256=source.expected_sha256,
                    max_bytes=source.max_bytes,
                )
            except RetrievalFailure as error:
                raise error.with_content_refs(*recorded_refs) from error
            corroboration.append(retrieved.response)
            recorded_refs.append(retrieved.response.content_ref)
        primary_publisher = PublisherIdentity.from_url(locator.source_url)
        if not any(
            PublisherIdentity.from_url(item.requested_url) != primary_publisher
            for item in corroboration
        ):
            raise RetrievalFailure(
                RetrievalOutcome.CONFLICT,
                "corroboration has no independently published retrieval",
                tuple(recorded_refs),
            )
        return AuthorityVerification(
            EvidenceAuthority.CORROBORATED,
            tuple(corroboration),
            tuple(recorded_refs),
        )


class ExternalAuthorityVerifier:
    def __init__(
        self,
        repository: RepositoryEndorsementVerifier,
        corroborated: CorroboratedAuthorityVerifier,
    ) -> None:
        self.repository = repository
        self.corroborated = corroborated

    @singledispatchmethod
    def verify(
        self,
        authority: AuthorityBasis,
        locator: ExternalUrlLocator,
        primary: RecordedHttpContent,
    ) -> AuthorityVerification:
        raise TypeError(f"unsupported external authority type: {type(authority).__name__}")

    @verify.register
    def _original_publisher(self, authority: OriginalPublisherAuthority,
                            locator: ExternalUrlLocator, primary: RecordedHttpContent) -> AuthorityVerification:
        verify_publisher(authority, primary.response.resolved_url)
        return AuthorityVerification(EvidenceAuthority.PRIMARY, (), (primary.response.content_ref,))

    @verify.register
    def _repository_endorsement(
        self,
        authority: RepositoryEndorsementAuthority,
        locator: ExternalUrlLocator,
        primary: RecordedHttpContent,
    ) -> AuthorityVerification:
        return self.repository.verify(authority, locator, primary)

    @verify.register
    def _corroborated(
        self,
        authority: CorroboratedAuthority,
        locator: ExternalUrlLocator,
        primary: RecordedHttpContent,
    ) -> AuthorityVerification:
        return self.corroborated.verify(authority, locator, primary)


def verify_publisher(authority: OriginalPublisherAuthority, resolved_url: str) -> None:
    """Bind the worker's source assessment to the actual retrieval host; no mirror required."""
    publisher = PublisherIdentity.from_url(authority.publisher_url).hostname
    actual = PublisherIdentity.from_url(resolved_url).hostname
    if actual != publisher and not actual.endswith("." + publisher):
        raise RetrievalFailure(RetrievalOutcome.CONFLICT,
                               "document redirected outside the assessed original publisher")
