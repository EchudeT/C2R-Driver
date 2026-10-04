"""Docker start events accelerate live inspection; events alone never prove QEMU ran."""

import json
import subprocess
import tempfile
import threading


class ContainerEvents:
    def __init__(self, docker, since, observe, error, diagnostics=None):
        self.observe, self.error = observe, error
        self.closing = False
        self.diagnostics = diagnostics
        if diagnostics is not None:
            diagnostics.parent.mkdir(parents=True, exist_ok=True)
        self.stderr = (  # closed by close(), after the reader thread joins
            diagnostics.open("w+") if diagnostics else tempfile.TemporaryFile(mode="w+")  # noqa: SIM115
        )
        try:
            self.process = subprocess.Popen(
                [
                    docker,
                    "events",
                    "--since",
                    since,
                    "--filter",
                    "type=container",
                    "--filter",
                    "event=start",
                    "--format",
                    "{{json .}}",
                ],
                stdout=subprocess.PIPE,
                stderr=self.stderr,
                text=True,
            )
        except OSError:
            self.stderr.close()
            raise
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        for line in self.process.stdout:
            try:
                event = json.loads(line)
                identifier = event.get("Actor", {}).get("ID") or event.get("id")
                if identifier:
                    self.observe(identifier)
            except (ValueError, TypeError, AttributeError) as error:
                self.error(f"container event: {error}")
        code = self.process.wait()
        if not self.closing and code:
            self.stderr.seek(0)
            detail = self.stderr.read(4000)
            self.error(f"Docker event stream exited {code}: {detail}; stderr: {self.diagnostics}")

    def close(self):
        self.closing = True
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.thread.join(timeout=10)
        if not self.thread.is_alive():
            self.process.stdout.close()
            self.stderr.close()
