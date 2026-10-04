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
from pathlib import Path


class Guest:
    def __init__(self, argv, output, timeout):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.argv = argv
        self.deadline = time.monotonic() + timeout
        self.selector = selectors.DefaultSelector()
        self.serial = bytearray()
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

    def assert_guest(self, command):
        # The complete marker never occurs in the sent command, so terminal echo cannot pass.
        # A fresh nonce binds the observation to this invocation, not an earlier command.
        nonce = uuid.uuid4().hex
        marker = b"\nDPF_CHECK_" + nonce.encode() + b":"
        pattern = re.compile(re.escape(marker) + rb"([0-9]+)\r?\n")
        self.send(
            "sh -c "
            + shlex.quote(command)
            + "; __dpf_exit=$?; printf '\\nDPF_CHECK_%s:%s\\n' "
            + nonce
            + ' "$__dpf_exit"\n'
        )
        self.until(lambda: pattern.search(self.serial) is not None, "GUEST_ASSERT result")
        code = int(pattern.search(self.serial).group(1))
        observation = {"command": command, "nonce": nonce, "exit_code": code}
        with (self.output / "guest-assertions.jsonl").open("a") as stream:
            stream.write(json.dumps(observation) + "\n")
        if code:
            raise RuntimeError(f"GUEST_ASSERT_FAILED: exit={code}; command={command!r}")

    def steps(self, steps):
        for step in steps:
            action, value = next(iter(step.items()))
            if action == "wait_serial":
                self.until(lambda value=value: value.encode() in self.serial, f"SERIAL {value!r}")
            elif action == "send_serial":
                self.send(value)
            elif action == "guest_assert":
                self.assert_guest(value)
            elif action == "wait_event":
                self.wait_event(value)
            elif action == "qmp":
                self.command(value["execute"], value.get("arguments"))
            elif action == "observe_seconds":
                end = time.monotonic() + value
                if end > self.deadline:
                    raise RuntimeError("TIMEOUT: observation exceeds case deadline")
                while time.monotonic() < end:
                    self.pump()
            elif action == "assert_no_event" and self.event(value):
                raise RuntimeError(f"UNEXPECTED_EVENT: {value}")

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


def validate_case(case):
    if not isinstance(case, dict) or set(case) - {"devices", "steps", "timeout_seconds"}:
        raise ValueError("Case accepts devices, steps and timeout_seconds only")
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
    if action in {"wait_serial", "send_serial", "guest_assert", "wait_event", "assert_no_event"}:
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValueError("Text action requires 1..4096 characters")
    elif action == "observe_seconds":
        if type(value) not in (int, float) or not 0 < value <= timeout:
            raise ValueError("Invalid observation duration")
    elif action == "qmp":
        if not isinstance(value, dict) or not isinstance(value.get("execute"), str):
            raise ValueError("QMP action requires execute")
    else:
        raise ValueError(f"Unknown case action: {action}")


def run(config, artifact, case, output):
    validate_case(case)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    argv = [config["qemu"], *config["qemu_args"], "-cdrom", str(artifact)]
    for device in case.get("devices", []):
        argv.extend(["-device", device])
    result = {"status": "FAIL", "scope": "guest-transport-and-declared-assertions"}
    start = time.monotonic()
    try:
        with Guest(argv, output, case.get("timeout_seconds", 120)) as guest:
            guest.steps(case["steps"])
            result.update(status="PASS", qmp_connected=True, serial_bytes=len(guest.serial))
    except (OSError, ValueError, RuntimeError) as error:
        result["error"] = str(error)
    finally:
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
