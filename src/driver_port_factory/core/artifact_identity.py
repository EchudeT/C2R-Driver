"""Canonical identity for packaged artifacts.

Occurrence numbers, attempt receipts and worker-report pointers are audit
metadata.  Runtime bytes, checker identity, implementation binding and
packaged variant contents are the substantive artifact identity used by
downstream execution guards.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy


def substantive_artifact_identity(identity: dict) -> dict:
    value = deepcopy(identity)
    value.pop("attempt_sha256", None)
    value.pop("work_report", None)
    variants = value.get("variants")
    if isinstance(variants, dict):
        value["variants"] = {
            path: {"digest": item.get("digest")}
            for path, item in sorted(variants.items())
            if isinstance(item, dict)
        }
    return value


def substantive_artifact_identity_digest(identity: dict) -> str:
    value = substantive_artifact_identity(identity)
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
