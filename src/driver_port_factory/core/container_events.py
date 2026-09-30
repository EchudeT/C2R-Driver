"""Docker start events accelerate live inspection; events alone never prove QEMU ran."""

import json
import subprocess
import threading


class ContainerEvents:
    def __init__(self, docker, since, observe, error):
        self.observe, self.error = observe, error
        self.process = subprocess.Popen(
            [docker, "events", "--since", since, "--filter", "type=container",
             "--filter", "event=start", "--format", "{{json .}}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
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
        if self.process.poll() not in {None, 0, -15}:
            self.error("Docker event stream exited; periodic inspection remains active")

    def close(self):
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
