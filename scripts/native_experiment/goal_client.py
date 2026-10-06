"""Thin client for Codex's native goal. No planning, review or custom continuation prompt."""

import json
import os
import queue
import signal
import subprocess
import threading
import time

from comparison_metrics import GoalState


def pump(stream, inbox):
    try:
        for line in stream:
            inbox.put(json.loads(line))
    except (ValueError, OSError) as error:
        inbox.put({"transport_error": str(error)})
    finally:
        inbox.put(None)


def terminate(process):
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


class GoalClient:
    def __init__(self, argv, directory):
        self.directory = directory
        self.state = GoalState()
        self.inbox = queue.Queue()
        self.counter = 0
        self.stderr = (directory / "codex.stderr.log").open("w")
        self.events = (directory / "events.jsonl").open("w")
        self.process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            text=True,
            start_new_session=True,
        )
        threading.Thread(target=pump, args=(self.process.stdout, self.inbox), daemon=True).start()

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def receive(self, timeout=1):
        try:
            message = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return {}
        if message is None or "transport_error" in message:
            raise RuntimeError("Codex app-server ended or emitted invalid JSON")
        self.events.write(json.dumps(message) + "\n")
        self.events.flush()
        self.state.observe(message)
        if "method" in message and "id" in message:
            # Never silently invent an answer to a request for human input.
            raise RuntimeError("Unexpected client request: " + message["method"])
        return message

    def request(self, method, params, timeout=30):
        self.counter += 1
        identifier = self.counter
        self.send({"id": identifier, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.receive()
            if response.get("id") == identifier:
                if "error" in response:
                    raise RuntimeError(method + ": " + json.dumps(response["error"]))
                return response["result"]
        raise TimeoutError("Codex request timed out: " + method)

    def start(self, workspace, protocol, prompt):
        self.request(
            "initialize",
            {
                "clientInfo": {"name": "c2r-experiment", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        self.send({"method": "initialized", "params": {}})
        result = self.request(
            "thread/start",
            {
                "cwd": str(workspace),
                "model": protocol["model"],
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
            },
        )
        self.thread_id = result["thread"]["id"]
        objective = (
            f"Translate Linux {protocol['driver']} into the hollow Asterinas target in Rust. "
            "Preserve source semantics within the frozen scope and integrate real device behavior; "
            "pass the provided unchanged public suite. Follow PROMPT.md. "
            "Do not derive the implementation solely from tests."
        )
        result = self.request(
            "thread/goal/set",
            {
                "threadId": self.thread_id,
                "objective": objective,
                "status": "active",
            },
        )
        self.state.goal = result["goal"]
        self.request(
            "turn/start",
            {
                "threadId": self.thread_id,
                "effort": protocol["reasoning_effort"],
                "input": [{"type": "text", "text": prompt}],
            },
        )

    def wait(self, started, limits, rates):
        while True:
            reason = self.state.stop_reason(time.monotonic() - started, limits, rates)
            if reason:
                return reason
            self.receive()

    def stop(self, reason):
        if getattr(self, "thread_id", None) and reason in {"TIME_LIMIT", "COST_LIMIT"}:
            result = self.request(
                "thread/goal/set",
                {
                    "threadId": self.thread_id,
                    "status": "budgetLimited",
                },
                timeout=5,
            )
            self.state.goal = result["goal"]
            if self.state.turn_active:
                self.request(
                    "turn/interrupt",
                    {
                        "threadId": self.thread_id,
                        "turnId": self.state.turn_id,
                    },
                    timeout=5,
                )

    def close(self):
        terminate(self.process)
        for stream in (self.process.stdin, self.process.stdout, self.stderr, self.events):
            stream.close()
