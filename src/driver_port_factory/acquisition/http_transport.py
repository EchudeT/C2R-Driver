from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from dataclasses import dataclass

from .facets import RetrievalOutcome
from .material_content import MaterialContentPolicy
from .retrieval_result import RetrievalFailure


@dataclass(frozen=True, slots=True)
class HttpTransportPolicy:
    user_agent: str = "Driver-Port-Factory/0.1"
    timeout_seconds: int = 60


@dataclass(frozen=True, slots=True)
class HttpResponseMetadata:
    requested_url: str
    resolved_url: str
    sha256: str
    size_bytes: int
    media_type: str


@dataclass(frozen=True, slots=True)
class DownloadedHttpContent:
    data: bytes
    response: HttpResponseMetadata


class HttpTransport:
    def __init__(self, policy: HttpTransportPolicy | None = None) -> None:
        self.policy = policy or HttpTransportPolicy()

    def download(self, source_url: str, *, max_bytes: int) -> DownloadedHttpContent:
        request = urllib.request.Request(
            source_url,
            headers={"User-Agent": self.policy.user_agent},
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.policy.timeout_seconds,
            ) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise RetrievalFailure(
                        RetrievalOutcome.FAILED,
                        f"download exceeds declared maximum of {max_bytes} bytes",
                    )
                media_type = response.headers.get_content_type().lower()
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise RetrievalFailure(
                        RetrievalOutcome.FAILED,
                        f"download exceeds declared maximum of {max_bytes} bytes",
                    )
                resolved_url = response.geturl()
        except urllib.error.HTTPError as error:
            raise RetrievalFailure(
                _http_failure_outcome(error.code),
                f"HTTP retrieval failed with status {error.code}",
            ) from error
        except urllib.error.URLError as error:
            raise RetrievalFailure(
                RetrievalOutcome.FAILED,
                f"HTTP retrieval failed: {error.reason}",
            ) from error
        MaterialContentPolicy.validate(data, media_type, source_url)
        return DownloadedHttpContent(
            data,
            HttpResponseMetadata(
                source_url,
                resolved_url,
                hashlib.sha256(data).hexdigest(),
                len(data),
                media_type,
            ),
        )


def _http_failure_outcome(status_code: int) -> RetrievalOutcome:
    if status_code in {401, 403}:
        return RetrievalOutcome.ACCESS_RESTRICTED
    if status_code in {404, 410}:
        return RetrievalOutcome.NOT_FOUND
    return RetrievalOutcome.FAILED
