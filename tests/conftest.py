"""Local fixture servers must not be sent through a developer's HTTP proxy."""
import os

import pytest


@pytest.fixture(autouse=True)
def local_fixture_proxy_bypass(monkeypatch):
    for name in ("NO_PROXY", "no_proxy"):
        entries = [entry for entry in os.environ.get(name, "").split(",") if entry]
        entries.extend(host for host in ("127.0.0.1", "localhost", "::1") if host not in entries)
        monkeypatch.setenv(name, ",".join(entries))
