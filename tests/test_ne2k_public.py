"""Offline socket/oracle tests, not translated NE2000 or QEMU driver results."""

import json
import socket
import struct

import pytest

from driver_port_factory.platform import guest, public_tests, suite
from driver_port_factory.platform.network_peer import WirePeer, request, response
from tests.test_prepared_public_tests import project_at


def receive_frame(connection):
    def exact(n):
        result = bytearray()
        while len(result) < n:
            chunk = connection.recv(n - len(result))
            assert chunk
            result.extend(chunk)
        return bytes(result)

    return exact(struct.unpack("!I", exact(4))[0])


def test_socket_oracle_replies_burst_and_rejects_missing_or_duplicate(tmp_path):
    peer = WirePeer(tmp_path)
    try:
        with socket.create_connection(("127.0.0.1", peer.port), timeout=2) as connection:
            for seq, length in [(0, 61), (2000, 60)]:
                packet = request(seq, length)
                data = struct.pack("!I", len(packet)) + packet
                # Socket boundaries must not become Ethernet frame boundaries.
                connection.sendall(data[:3])
                connection.sendall(data[3:])
                replies = range(2000, 2016) if seq == 2000 else [seq]
                for reply_seq in replies:
                    assert receive_frame(connection) == response(request(reply_seq, length))
        peer.close()
        peer.assert_expected([[0, 61], [2000, 60]])
        for wrong in ([[0, 61]], [[0, 61], [2000, 60], [2000, 60]]):
            with pytest.raises(RuntimeError, match="WIRE_MISMATCH"):
                peer.assert_expected(wrong)
        peer.frames[0]["valid"] = False
        with pytest.raises(RuntimeError, match="WIRE_MISMATCH"):
            peer.assert_expected([[0, 61], [2000, 60]])
    finally:
        peer.close()


def test_ne2k_prepared_cases_and_fixture(tmp_path):
    project = project_at(tmp_path)
    project.config.driver_name = "ne2k-pci"
    worktree = tmp_path / "work"
    suite.install(worktree)
    public_tests.install(project, worktree)
    public_tests.verify(project, worktree, final=True)
    assert (worktree / "kernel/core/comps/network/src/dpf_ne2k_public.rs").is_file()
    rows = json.loads((public_tests.assets(project) / "cases.json").read_text())
    assert [r["id"] for r in rows] == ["ne2k-probe", "ne2k-traffic", "ne2k-recovery"]
    for row in rows:
        guest.validate_case(row["case"])
    assert len(rows[1]["case"]["network_peer"]["expected_exchanges"]) == 97
    assert len(rows[2]["case"]["network_peer"]["expected_exchanges"]) == 3
