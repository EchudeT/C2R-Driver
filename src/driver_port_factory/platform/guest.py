#!/usr/bin/env python3
"""Standalone guest transport, also runs inside the selected container (stdlib only)."""

import argparse
import json
import os
import re
import selectors
import shlex
import socket
import subprocess
import tempfile
import time
import uuid

try:
    from .network_peer import WirePeer
except ImportError:  # Standalone copy inside the selected Docker container.
    from network_peer import WirePeer
from pathlib import Path

# Strip display color only; never join lines, erase output, or interpret terminal commands.
COLOR = re.compile(rb"\x1b\[[0-9;]*m")


def log_text(data):
    return COLOR.sub(b"", data).decode(errors="replace")


def diagnostic_excerpt(data):
    text = log_text(data)
    lines = text.splitlines()
    useful = [
        line
        for line in lines
        if re.search(r"not implemented|not found|error|failed|denied", line, re.IGNORECASE)
    ]
    return ("\n".join(useful)[-1000:] + "\n" + text[-800:]).strip()


def matches(actual, expected):
    """Declared object subset; scalar/list values match exactly (bool is not integer)."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and matches(actual[k], v) for k, v in expected.items()
        )
    return type(actual) is type(expected) and actual == expected


class Guest:
    def __init__(self, argv, output, timeout, ready_text=None):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.argv = argv
        self.deadline = time.monotonic() + timeout
        self.selector = selectors.DefaultSelector()
        self.serial = bytearray()
        self.boot_serial = bytearray()
        self.boot_closed = False
        self.ready_text = ready_text
        self.assertion_output = None
        self.buffer = bytearray()
        self.messages = []
        self.consumed_events = set()
        self.process = None
        self.qmp = None
        self.temporary = None
        self.logs = []
        self.counter = 0

    def __enter__(self):
        try:
            # AF_UNIX addresses stay short regardless of the host workspace length.
            self.temporary = tempfile.TemporaryDirectory(prefix="dpf-qmp-", dir="/tmp")
            address = str(Path(self.temporary.name) / "q.sock")
            self.serial_log = (self.output / "serial.log").open("wb", buffering=0)
            self.qmp_log = (self.output / "qmp.jsonl").open("wb", buffering=0)
            self.stderr = (self.output / "qemu-stderr.log").open("wb", buffering=0)
            self.logs = [self.serial_log, self.qmp_log, self.stderr]
            command = [*self.argv, "-S", "-qmp", f"unix:{address},server=on,wait=off"]
            (self.output / "argv.json").write_text(json.dumps(command))
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr
            )
            self.selector.register(self.process.stdout, selectors.EVENT_READ, "serial")
            self.qmp = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connect_deadline = min(self.deadline, time.monotonic() + 10)
            while True:
                try:
                    self.qmp.connect(address)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if self.process.poll() is not None or time.monotonic() >= connect_deadline:
                        raise RuntimeError("QMP_UNAVAILABLE: inspect qemu-stderr.log") from None
                    time.sleep(0.05)
            self.qmp.setblocking(False)
            self.selector.register(self.qmp, selectors.EVENT_READ, "qmp")
            self.until(lambda: any("QMP" in row for row in self.messages), "QMP_GREETING")
            self.command("qmp_capabilities")
            self.command("query-status")
            self.command("cont")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def pump(self, timeout=0.1):
        for key, _ in self.selector.select(timeout):
            chunk = os.read(key.fileobj.fileno(), 65536)
            if not chunk:
                self.selector.unregister(key.fileobj)
            elif key.data == "serial":
                self.serial_log.write(chunk)
                self.serial.extend(chunk)
                if not self.boot_closed:
                    self.boot_serial.extend(chunk)
                    if self.ready_text and self.ready_text in log_text(self.boot_serial):
                        self.boot_closed = True
                if self.assertion_output is not None:
                    self.assertion_output.extend(chunk)
                    del self.assertion_output[:-16384]
                # Logs are complete; matching window is bounded for long-running guests.
                del self.serial[:-1048576]
            else:
                self.buffer.extend(chunk)
                while b"\n" in self.buffer:
                    line, _, self.buffer = self.buffer.partition(b"\n")
                    if line.strip():
                        value = json.loads(line)
                        self.messages.append(value)
                        self.qmp_log.write(line + b"\n")
                if len(self.buffer) > 1048576:
                    raise RuntimeError("QMP_PROTOCOL_ERROR: oversized message")

    def until(self, predicate, label):
        while not predicate():
            self.pump()
            if predicate():
                return
            if time.monotonic() >= self.deadline:
                raise RuntimeError(f"TIMEOUT: {label}")
            if self.process.poll() is not None:
                raise RuntimeError(f"GUEST_EXITED: {label}, code={self.process.returncode}")

    def command(self, name, arguments=None):
        self.counter += 1
        request = {"execute": name, "id": self.counter}
        if arguments is not None:
            request["arguments"] = arguments
        self.qmp.sendall(json.dumps(request).encode() + b"\n")
        self.until(lambda: any(r.get("id") == self.counter for r in self.messages), name)
        response = next(r for r in self.messages if r.get("id") == self.counter)
        if "error" in response:
            raise RuntimeError(f"QMP_ERROR: {response['error']}")
        return response.get("return")

    def send(self, text):
        # Freeze BEFORE sending anything: neither command echo nor its output is a boot fact.
        self.boot_closed = True
        self.process.stdin.write(text.encode())
        self.process.stdin.flush()

    def event(self, name):
        return any(r.get("event") == name for r in self.messages)

    def wait_event(self, name):
        def available():
            return [
                i
                for i, row in enumerate(self.messages)
                if row.get("event") == name and i not in self.consumed_events
            ]

        self.until(lambda: bool(available()), f"EVENT {name}")
        self.consumed_events.add(available()[0])

    def assert_boot_log(self, text):
        self.until(
            lambda: text in log_text(self.boot_serial) or self.boot_closed, f"BOOT_LOG {text!r}"
        )
        if text not in log_text(self.boot_serial):
            raise RuntimeError(f"BOOT_LOG_NOT_FOUND: {text!r}; boot output already ended")

    def expect_event(self, expected):
        def available():
            return [
                i
                for i, row in enumerate(self.messages)
                if i not in self.consumed_events and matches(row, expected)
            ]

        self.until(lambda: bool(available()), f"EVENT_MATCH {expected!r}")
        self.consumed_events.add(available()[0])

    def assert_qmp(self, specification):
        actual = self.command(specification["execute"], specification.get("arguments"))
        if not matches(actual, specification["match"]):
            raise RuntimeError(
                f"QMP_ASSERT_FAILED: {specification['execute']}; "
                f"expected={specification['match']!r}; actual={actual!r}"
            )

    def assert_guest(self, command):
        # The complete marker never occurs in the sent command, so terminal echo cannot pass.
        # A fresh nonce binds the observation to this invocation, not an earlier command.
        nonce = uuid.uuid4().hex
        marker = b"\nDPF_CHECK_" + nonce.encode() + b":"
        # The guest TTY and serial console may each expand LF to CRLF (CRCRLF).
        pattern = re.compile(re.escape(marker) + rb"([0-9]+)\r*\n")
        self.assertion_output = bytearray()
        self.send(
            "sh -c "
            + shlex.quote(command)
            + "; __dpf_exit=$?; printf '\\nDPF_CHECK_%s:%s\\n' "
            + nonce
            + ' "$__dpf_exit"\n'
        )
        self.until(lambda: pattern.search(self.serial) is not None, "GUEST_ASSERT result")
        code = int(pattern.search(self.serial).group(1))
        excerpt = diagnostic_excerpt(self.assertion_output)
        self.assertion_output = None
        observation = {
            "command": command,
            "nonce": nonce,
            "exit_code": code,
            "output_excerpt": excerpt,
            "raw_log": str(self.output / "serial.log"),
        }
        with (self.output / "guest-assertions.jsonl").open("a") as stream:
            stream.write(json.dumps(observation) + "\n")
        if code:
            raise RuntimeError(
                f"GUEST_ASSERT_FAILED: exit={code}; command={command!r}"
                f"\nGuest output (untrusted):\n{excerpt}"
            )

    def observe(self, seconds):
        end = time.monotonic() + seconds
        if end > self.deadline:
            raise RuntimeError("TIMEOUT: observation exceeds case deadline")
        while time.monotonic() < end:
            self.pump()

    def assert_no_event(self, name):
        if self.event(name):
            raise RuntimeError(f"UNEXPECTED_EVENT: {name}")

    def assert_event_counts(self, expected):
        actual = {name: sum(r.get("event") == name for r in self.messages) for name in expected}
        if actual != expected:
            raise RuntimeError(f"EVENT_COUNTS: expected={expected!r}; actual={actual!r}")

    def wait_serial(self, text):
        self.until(lambda: text.encode() in self.serial, f"SERIAL {text!r}")

    def assert_serial_matches(self, specification):
        actual = [
            list(row) if isinstance(row, tuple) else row
            for row in re.findall(specification["pattern"], log_text(self.serial))
        ]
        if actual != specification["expected"]:
            raise RuntimeError(
                f"SERIAL_MATCHES: expected={specification['expected']!r}; actual={actual!r}"[:2500]
            )

    def steps(self, steps):
        actions = {
            "wait_serial": self.wait_serial,
            "assert_boot_log": self.assert_boot_log,
            "send_serial": self.send,
            "guest_assert": self.assert_guest,
            "wait_event": self.wait_event,
            "expect_event": self.expect_event,
            "qmp_assert": self.assert_qmp,
            "qmp": lambda spec: self.command(spec["execute"], spec.get("arguments")),
            "observe_seconds": self.observe,
            "assert_no_event": self.assert_no_event,
            "assert_event_counts": self.assert_event_counts,
            "assert_serial_matches": self.assert_serial_matches,
        }
        for step in steps:
            action, value = next(iter(step.items()))
            actions[action](value)

    def __exit__(self, *_):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            for stream in (self.process.stdin, self.process.stdout):
                stream.close()
        if self.qmp is not None:
            self.qmp.close()
        self.selector.close()
        for stream in self.logs:
            stream.close()
        if self.temporary is not None:
            self.temporary.cleanup()
        # Archive after cleanup: a full disk must not leave QEMU running.
        (self.output / "boot-serial.log").write_bytes(self.boot_serial)


def validate_case(case):
    if not isinstance(case, dict) or set(case) - {
        "devices",
        "steps",
        "timeout_seconds",
        "network_peer",
    }:
        raise ValueError("Case accepts devices, steps and timeout_seconds only")
    if "network_peer" in case:
        peer = case["network_peer"]
        if (
            not isinstance(peer, dict)
            or set(peer) != {"expected_exchanges"}
            or not isinstance(peer["expected_exchanges"], list)
            or len(peer["expected_exchanges"]) > 200
            or any(
                not isinstance(row, list)
                or len(row) != 2
                or type(row[0]) is not int
                or not 0 <= row[0] <= 65535
                or type(row[1]) is not int
                or not 60 <= row[1] <= 1514
                for row in peer["expected_exchanges"]
            )
        ):
            raise ValueError("network_peer requires bounded sequence/length pairs")
    timeout = case.get("timeout_seconds", 120)
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("Case timeout must be 1..600 seconds")
    devices = case.get("devices", [])
    if not isinstance(devices, list) or any(
        not isinstance(d, str) or not d or d.startswith("-") for d in devices
    ):
        raise ValueError("devices must be QEMU -device values")
    steps = case.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > 100:
        raise ValueError("Case requires 1..100 steps")
    for step in steps:
        validate_step(step, timeout)
    return case


def validate_step(step, timeout):
    if not isinstance(step, dict) or len(step) != 1:
        raise ValueError("Each step has exactly one action")
    action, value = next(iter(step.items()))
    if action in {
        "wait_serial",
        "assert_boot_log",
        "send_serial",
        "guest_assert",
        "wait_event",
        "assert_no_event",
    }:
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValueError("Text action requires 1..4096 characters")
    elif action == "assert_event_counts":
        if (
            not isinstance(value, dict)
            or not value
            or any(
                not isinstance(k, str) or not k or type(v) is not int or v < 0
                for k, v in value.items()
            )
        ):
            raise ValueError("assert_event_counts needs event names and nonnegative integer counts")
    elif action in {"expect_event", "qmp_assert"}:
        validate_expectation(action, value)
    elif action == "assert_serial_matches":
        validate_serial_matches(value)
    elif action == "observe_seconds":
        if type(value) not in (int, float) or not 0 < value <= timeout:
            raise ValueError("Invalid observation duration")
    elif action == "qmp":
        if not isinstance(value, dict) or not isinstance(value.get("execute"), str):
            raise ValueError("QMP action requires execute")
    else:
        raise ValueError(f"Unknown case action: {action}")


def validate_serial_matches(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"pattern", "expected"}
        or not isinstance(value["pattern"], str)
        or not 1 <= len(value["pattern"]) <= 4096
        or not isinstance(value["expected"], list)
        or len(value["expected"]) > 200
    ):
        raise ValueError("Serial assertion requires a bounded pattern and ordered expected list")
    try:
        re.compile(value["pattern"])
    except re.error as error:
        raise ValueError(f"Invalid serial assertion pattern: {error}") from error


def validate_expectation(action, value):
    if not isinstance(value, dict) or not value:
        raise ValueError(f"Invalid {action} specification: requires a nonempty object")
    if action == "expect_event":
        if (
            set(value) - {"event", "data"}
            or not isinstance(value.get("event"), str)
            or not value["event"]
            or not isinstance(value.get("data", {}), dict)
        ):
            raise ValueError("expect_event needs event name and optional data fields to match")
    elif (
        set(value) - {"execute", "arguments", "match"}
        or not isinstance(value.get("execute"), str)
        or not value["execute"]
        or "match" not in value
        or not isinstance(value.get("arguments", {}), dict)
    ):
        raise ValueError("qmp_assert needs execute, optional arguments, and explicit match")


def run(config, artifact, case, output):
    validate_case(case)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    argv = [config["qemu"], *config["qemu_args"], "-cdrom", str(artifact)]
    for device in case.get("devices", []):
        argv.extend(["-device", device])
    result = {"status": "FAIL", "scope": "guest-transport-and-declared-assertions"}
    start = time.monotonic()
    peer = None
    try:
        if "network_peer" in case:
            peer = WirePeer(output)
            argv.extend(
                ["-nic", "none", "-netdev", f"socket,id=dpf-peer,connect=127.0.0.1:{peer.port}"]
            )
        with Guest(
            argv, output, case.get("timeout_seconds", 120), ready_text=config.get("ready_text")
        ) as guest:
            guest.steps(case["steps"])
        if peer is not None:
            peer.close()
            peer.assert_expected(case["network_peer"]["expected_exchanges"])
        result.update(status="PASS", qmp_connected=True, serial_bytes=len(guest.serial))
    except (OSError, ValueError, RuntimeError) as error:
        result["error"] = str(error)
    finally:
        if peer is not None:
            peer.close()
        result["duration_seconds"] = round(time.monotonic() - start, 3)
        (output / "guest-result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("config", "artifact", "case", "output"):
        parser.add_argument(name)
    args = parser.parse_args()
    value = run(
        json.loads(Path(args.config).read_text()),
        args.artifact,
        json.loads(Path(args.case).read_text()),
        args.output,
    )
    print(json.dumps(value))
    raise SystemExit(0 if value["status"] == "PASS" else 1)
