"""Synthetic oracle checks only; not an evbug translation or real QEMU validation."""

import json

import pytest

from driver_port_factory.platform import guest, public_tests, suite
from tests.test_prepared_public_tests import project_at


def test_evbug_prepared_fixture_and_exact_ordered_log_oracle(tmp_path):
    project = project_at(tmp_path)
    project.config.driver_name = "evbug"
    worktree = tmp_path / "work"
    suite.install(worktree)
    public_tests.install(project, worktree)
    public_tests.verify(project, worktree, final=True)
    assert (worktree / "kernel/core/comps/input/src/dpf_evbug_public.rs").is_file()
    rows = json.loads((public_tests.assets(project) / "cases.json").read_text())
    assert len(rows) == 3
    for row in rows:
        guest.validate_case(row["case"])
    spec = rows[0]["case"]["steps"][3]["assert_serial_matches"]
    observation = guest.Guest([], tmp_path, 1)
    logs = [
        f"evbug: Event. Dev: {n}, Type: {t}, Code: {c}, Value: {v}\n"
        for n, t, c, v in spec["expected"]
    ]
    try:
        observation.serial = bytearray("".join(logs).encode())
        observation.assert_serial_matches(spec)
        for wrong in [
            logs[:-1],
            logs + logs[:1],
            list(reversed(logs)),
            [s.replace("Value: -7", "Value: 7") for s in logs],
        ]:
            observation.serial = bytearray("".join(wrong).encode())
            with pytest.raises(RuntimeError, match="SERIAL_MATCHES"):
                observation.assert_serial_matches(spec)
    finally:
        observation.selector.close()
