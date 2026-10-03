"""Local echoing-shell fixtures verify assertions; no QEMU/model or driver claims."""

import json
import selectors
import subprocess
import sys
from unittest.mock import patch

import pytest

from driver_port_factory.platform.guest import Guest, validate_case


@pytest.mark.parametrize("mode", ["success", "failure", "echo-only", "stale"])
def test_guest_assertion_requires_fresh_executed_exit_not_echo(tmp_path, mode):
    program = (
        "import sys,subprocess\n"
        "for line in sys.stdin:\n"
        " print(line, end='', flush=True)\n"
        + (
            " subprocess.run(line, shell=True, check=False)\n"
            if mode in {"success", "failure"}
            else ""
        )
    )
    guest = Guest([], tmp_path, timeout=1)
    guest.process = subprocess.Popen(
        [sys.executable, "-u", "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    guest.serial_log = (tmp_path / "serial.log").open("wb", buffering=0)
    guest.logs = [guest.serial_log]
    guest.selector.register(guest.process.stdout, selectors.EVENT_READ, "serial")
    if mode == "stale":
        guest.serial.extend(b"\nDPF_CHECK_old_nonce:0\n")
    command = "test 'a b' = 'a b'" if mode == "success" else "false"
    validate_case({"steps": [{"guest_assert": command}]})
    try:
        if mode == "success":
            guest.steps([{"guest_assert": command}, {"guest_assert": command}])
            rows = [
                json.loads(line)
                for line in (tmp_path / "guest-assertions.jsonl").read_text().splitlines()
            ]
            assert [r["exit_code"] for r in rows] == [0, 0]
            assert rows[0]["nonce"] != rows[1]["nonce"]
        else:
            expected = "GUEST_ASSERT_FAILED: exit=1" if mode == "failure" else "TIMEOUT"
            with pytest.raises(RuntimeError, match=expected):
                guest.steps([{"guest_assert": command}])
    finally:
        guest.__exit__()


@pytest.mark.parametrize("line_end", ["\n", "\r\n", "\r\r\n"])
def test_assertion_marker_accepts_guest_crlf_but_rejects_nonzero(tmp_path, line_end):
    guest = Guest([], tmp_path, timeout=1)

    def send(command):
        import re

        nonce = re.search(r" ([a-f0-9]{32}) ", command).group(1)
        assert ("DPF_CHECK_" + nonce) not in command
        guest.serial.extend(f"{line_end}DPF_CHECK_{nonce}:7{line_end}".encode())

    with patch.object(guest, "send", side_effect=send), pytest.raises(RuntimeError, match="exit=7"):
        guest.assert_guest("exit 7")
    guest.__exit__()
