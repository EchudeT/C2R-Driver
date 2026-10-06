#!/usr/bin/python3.11
"""Transparent Docker CLI transport. The host daemon socket is never given to workers."""

import base64
import json
import os
import socket
import sys
import threading


def forward_input(connection):
    try:
        while chunk := os.read(0, 65536):
            connection.sendall(
                json.dumps({"stdin": base64.b64encode(chunk).decode()}).encode() + b"\n"
            )
        connection.sendall(b'{"stdin_eof":true}\n')
    except OSError:
        pass


def main():
    connection = socket.socket(socket.AF_UNIX)
    connection.connect(os.environ["C2R_DOCKER_BROKER"])
    connection.sendall(json.dumps({"argv": sys.argv[1:]}).encode() + b"\n")
    threading.Thread(target=forward_input, args=(connection,), daemon=True).start()
    with connection.makefile("r") as stream:
        for line in stream:
            message = json.loads(line)
            if "exit_code" in message:
                return message["exit_code"]
            if "error" in message:
                print(message["error"], file=sys.stderr)
                return 125
            os.write(message["fd"], base64.b64decode(message["data"]))
    return 125


if __name__ == "__main__":
    raise SystemExit(main())
