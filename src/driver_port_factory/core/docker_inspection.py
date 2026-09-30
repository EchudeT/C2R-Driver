"""Read-only local Docker API access, avoiding CLI startup on short process samples."""

import http.client
import json
import socket
from urllib.parse import quote


class DockerInspectionError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Docker inspection HTTP {status}")


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=3)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class DockerInspection:
    def __init__(self, endpoint):
        if not endpoint.startswith("unix://"):
            raise ValueError("direct inspection requires a local Unix Docker endpoint")
        self.path = endpoint.removeprefix("unix://")

    def get(self, path):
        connection = UnixConnection(self.path)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            value = json.loads(response.read())
            if response.status != 200:
                raise DockerInspectionError(response.status)
            return value
        finally:
            connection.close()

    def inspect(self, identifier):
        return self.get(f"/containers/{quote(identifier, safe='')}/json")

    def top(self, identifier):
        value = self.get(f"/containers/{quote(identifier, safe='')}/top?ps_args=-eo%20pid,args")
        return "\n".join(" ".join(row) for row in
                         [value["Titles"], *(value.get("Processes") or [])])
