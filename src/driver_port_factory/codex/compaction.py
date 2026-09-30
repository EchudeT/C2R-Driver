"""Observe native compaction at invocation boundaries; never gate work on diagnostics."""
import json
import os
from pathlib import Path

from .context_logs import locate_rollout

CURSOR = "native_compaction/cursor"


def refresh_inputs(thread, known):
    """Return dedup identities, cursor and observation; persist only with the next prompt.

    Read new complete records only. A compaction during the upcoming invocation is
    deliberately not acknowledged here: the next invocation must re-supply evidence.
    """
    if not thread:
        return known, None, None
    try:
        home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
        source = locate_rollout(home, thread)
        if source is None:
            return known, None, {"status": "unavailable"}
        prior = json.loads(known.get(CURSOR, "null"))
        with source.open("rb") as stream:
            first = json.loads(stream.readline())
            meta = first.get("payload", {})
            if first.get("type") != "session_meta" or (meta.get("id") or meta.get("session_id")) != thread:
                return known, None, {"status": "identity_mismatch"}
            stat = os.fstat(stream.fileno())
            identity = [thread, str(source), stat.st_dev, stat.st_ino]
            continuing = (isinstance(prior, dict) and prior.get("identity") == identity
                          and isinstance(prior.get("offset"), int)
                          and 0 <= prior["offset"] <= stat.st_size)
            stream.seek(prior["offset"] if continuing else 0)
            compacted = False
            while stream.tell() < stat.st_size:
                offset = stream.tell()
                line = stream.readline(stat.st_size - offset)
                if not line.endswith(b"\n"):
                    stream.seek(offset)
                    break
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(record, dict) and record.get("type") == "compacted":
                    compacted = True
            cursor = json.dumps({"identity": identity, "offset": stream.tell()}, sort_keys=True)
        # A replaced/truncated stream also invalidates dedup, without calling it compaction.
        reset = compacted or (prior is not None and not continuing)
        return ({} if reset else known), cursor, {
            "status": "compacted" if compacted else "log_replaced" if reset else "unchanged",
            "reinject": reset,
        }
    except (OSError, ValueError, TypeError, AttributeError):
        return known, None, {"status": "unavailable"}
