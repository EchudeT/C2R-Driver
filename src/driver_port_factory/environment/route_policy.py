from __future__ import annotations

from dataclasses import dataclass

from ..core.models import WorkflowError
from .models import RouteKind


@dataclass(frozen=True, slots=True)
class RouteEvidence:
    executable_name: str
    cited_paths: tuple[str, ...]
    cited_files: tuple[str, ...]
    source_root: str
    target_root: str
    target_worktree: str
    qemu_root: str

    def cites(self, *roots: str) -> bool:
        return any(
            path == root or path.startswith(root + "/")
            for root in roots
            for path in self.cited_paths
        )

    def require_file(self, route: str) -> None:
        if not self.cited_files:
            raise WorkflowError(f"{route} must cite a concrete frozen runner or metadata file")


class ExperimentRoutePolicy:
    """Validate route-specific executable and provenance requirements."""

    def validate(self, route: RouteKind, evidence: RouteEvidence) -> None:
        validators = {
            RouteKind.DIRECT_QEMU: self._direct_qemu,
            RouteKind.CONTAINERIZED_QEMU: self._containerized_qemu,
            RouteKind.OFFICIAL_TARGET_RUNNER: self._official_target_runner,
            RouteKind.SOURCE_BASELINE_RUNNER: self._source_baseline_runner,
            RouteKind.QTEST_OR_QMP_HARNESS: self._qtest_or_qmp,
        }
        validators[route](evidence)

    @staticmethod
    def _direct_qemu(evidence: RouteEvidence) -> None:
        if not (
            evidence.executable_name.startswith("qemu-system-")
            or evidence.executable_name == "qemu-storage-daemon"
        ):
            raise WorkflowError("direct-qemu route must execute a QEMU system binary")
        if not evidence.cites(evidence.qemu_root):
            raise WorkflowError("direct-qemu route must cite the frozen QEMU source checkout")

    @staticmethod
    def _containerized_qemu(evidence: RouteEvidence) -> None:
        if evidence.executable_name not in {"docker", "podman"}:
            raise WorkflowError("containerized-qemu route must execute docker or podman")
        evidence.require_file(RouteKind.CONTAINERIZED_QEMU.value)

    @staticmethod
    def _official_target_runner(evidence: RouteEvidence) -> None:
        if not evidence.cites(evidence.target_root, evidence.target_worktree):
            raise WorkflowError(
                "official-target-runner must cite frozen target metadata or its writable worktree"
            )
        evidence.require_file(RouteKind.OFFICIAL_TARGET_RUNNER.value)

    @staticmethod
    def _source_baseline_runner(evidence: RouteEvidence) -> None:
        if not evidence.cites(evidence.source_root):
            raise WorkflowError("source-baseline-runner must cite the frozen source checkout")
        evidence.require_file(RouteKind.SOURCE_BASELINE_RUNNER.value)

    @staticmethod
    def _qtest_or_qmp(evidence: RouteEvidence) -> None:
        if not evidence.cites(evidence.qemu_root):
            raise WorkflowError("qtest-or-qmp-harness must cite the frozen QEMU source checkout")
        evidence.require_file(RouteKind.QTEST_OR_QMP_HARNESS.value)
