from __future__ import annotations

from typing import Any

from .contracts import KnowledgeEvidenceStatus
from .index import KnowledgeIndex, file_sha256
from .probes import KnowledgeProbe


class KnowledgeProbeExecutor:
    def run(self, knowledge: KnowledgeIndex, probe: KnowledgeProbe) -> dict[str, Any]:
        result = knowledge.search(
            probe.query,
            domain=probe.domain,
            path_prefix=probe.path_prefix,
            limit=probe.limit,
        )
        matches = result["results"]
        expected_ids = set(probe.expected_record_ids)
        if expected_ids:
            matches = [match for match in matches if match["record_id"] in expected_ids]
        verification = self._verify_first_match(knowledge, matches)
        passed = bool(verification and verification["locator_valid"] and verification["hash_valid"])
        return {
            "probe_id": probe.probe_id,
            "topic": probe.topic,
            "domain": probe.domain.value,
            "query": probe.query,
            "required": probe.required,
            "result_count": len(matches),
            "verification": verification,
            "status": (KnowledgeEvidenceStatus.PASS if passed else KnowledgeEvidenceStatus.FAIL),
        }

    @staticmethod
    def _verify_first_match(
        knowledge: KnowledgeIndex, matches: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        if not matches:
            return None
        hit = matches[0]
        exact = knowledge.show(str(hit["chunk_id"]))["result"]
        original = knowledge.controlled_path(str(exact["path"]))
        lines = original.read_text(encoding="utf-8").splitlines()
        current_sha256 = file_sha256(original)
        return {
            "chunk_id": exact["chunk_id"],
            "record_id": exact["record_id"],
            "original_path": exact["path"],
            "line_start": exact["line_start"],
            "line_end": exact["line_end"],
            "revision": exact["revision"],
            "source_url": exact["source_url"],
            "sha256": exact["sha256"],
            "current_sha256": current_sha256,
            "locator_valid": (
                1 <= int(exact["line_start"]) <= int(exact["line_end"]) <= len(lines)
            ),
            "hash_valid": current_sha256 == exact["sha256"],
        }
