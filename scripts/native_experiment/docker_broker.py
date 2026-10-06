"""Private infrastructure for standard Docker commands, scoped to one experiment worker."""

import base64
import json
import selectors
import shutil
import socketserver
import subprocess
import threading
import uuid
from pathlib import Path

from docker_policy import validated_run
from goal_client import terminate


class DockerBroker(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path, image, writable, readonly):
        self.image = image
        self.writable = [Path(path).resolve() for path in writable]
        self.readonly = [Path(path).resolve() for path in readonly]
        self.owner = "c2r.worker=" + uuid.uuid4().hex
        self.docker = shutil.which("docker")
        super().__init__(str(path), Handler)

    def known(self):
        rows = subprocess.check_output(
            [
                self.docker,
                "ps",
                "-a",
                "--filter",
                "label=" + self.owner,
                "--format",
                "{{.ID}} {{.Names}}",
                "--no-trunc",
            ],
            text=True,
        ).splitlines()
        return {value for row in rows for value in row.split()}

    def command(self, argv):
        if argv[0] == "run":
            validated_run(argv, self.image, self.writable, self.readonly)
            return [self.docker, "run", "--label", self.owner, *argv[1:]]
        if argv[0] == "ps":
            return [self.docker, *argv, "--filter", "label=" + self.owner]
        if argv[0] == "events":
            return [self.docker, *argv, "--filter", "label=" + self.owner]
        if argv[0] in {"inspect", "top", "rm"}:
            names = [arg for arg in argv[1:] if not arg.startswith("-")]
            # top's final format is harmless; only the first container is addressed.
            identifiers = names[:1] if argv[0] == "top" else names
            if not identifiers or not set(identifiers) <= self.known():
                raise ValueError("Container is not owned by this experiment")
            return [self.docker, *argv]
        if argv[:2] == ["image", "inspect"] and argv[-1] == self.image:
            return [self.docker, *argv]
        if argv[0] in {"version", "info"}:
            return [self.docker, *argv]
        if argv[:2] == ["context", "inspect"]:
            return ["/bin/echo", "unix:///var/run/docker.sock"]
        raise ValueError("Docker operation unavailable to workers: " + argv[0])


class Handler(socketserver.StreamRequestHandler):
    def send(self, value):
        self.wfile.write(json.dumps(value).encode() + b"\n")
        self.wfile.flush()

    def input(self, process):
        try:
            for line in self.rfile:
                message = json.loads(line)
                if "stdin_eof" in message:
                    break
                process.stdin.write(base64.b64decode(message["stdin"]))
                process.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            process.stdin.close()

    def handle(self):
        process = None
        try:
            request = json.loads(self.rfile.readline())
            argv = self.server.command(request["argv"])
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            threading.Thread(target=self.input, args=(process,), daemon=True).start()
            self.output(process)
        except (OSError, ValueError, KeyError, IndexError) as error:
            try:
                self.send({"error": str(error)})
            except OSError:
                pass
        finally:
            if process:
                terminate(process)

    def output(self, process):
        with selectors.DefaultSelector() as poller:
            poller.register(process.stdout, selectors.EVENT_READ, 1)
            poller.register(process.stderr, selectors.EVENT_READ, 2)
            while poller.get_map():
                for key, _ in poller.select(timeout=1):
                    chunk = key.fileobj.read1(65536)
                    if not chunk:
                        poller.unregister(key.fileobj)
                    else:
                        self.send({"fd": key.data, "data": base64.b64encode(chunk).decode()})
            self.send({"exit_code": process.wait()})
