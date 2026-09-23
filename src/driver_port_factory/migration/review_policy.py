"""Worker self-check protocol, separate from independent functional review."""
from ..codex.contracts import CodexOutputError
def require_self_review(text: str) -> None:
    if not text.strip():
        raise CodexOutputError(
            "Complete the Skill's self-check in the report before submitting the pass decision. "
            "A self-check does not replace the independent final review."
        )
