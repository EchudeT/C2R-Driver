"""Offline Docker protocol fixture. Trace bytes here are synthetic, not device evidence."""

DOCKER_STUB = r"""
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_CALLS"], "a") as log:
    log.write(json.dumps(args) + "\n")
state_path = pathlib.Path(os.environ["FAKE_DOCKER_STATE"])
def save(state):
    state_path.write_text(json.dumps(state))
if args[:2] == ["image", "inspect"]:
    print(os.environ["FAKE_IMAGE_ID"])
elif args[0] == "create":
    root = args[args.index("-w") + 1]
    image = os.environ.get("FAKE_CREATED_IMAGE", os.environ["FAKE_IMAGE_ID"])
    state = {"Id": "c" * 64, "Image": image,
             "Mounts": [{"Type": "bind", "Source": root, "Destination": root}],
             "State": {"Running": False, "ExitCode": 0, "Error": ""},
             "probe": args[-1], "root": root}
    save(state)
    print(state["Id"])
elif args[0] == "inspect":
    print(json.dumps([json.loads(state_path.read_text())]))
elif args[0] == "start":
    state = json.loads(state_path.read_text())
    state["State"]["Running"] = True
    save(state)
    result = subprocess.run(["/bin/bash", state["probe"]], cwd=state["root"])
    state["State"].update(Running=False, ExitCode=result.returncode)
    save(state)
    sys.exit(result.returncode)
elif args[0] == "kill":
    state = json.loads(state_path.read_text())
    state["State"].update(Running=False, ExitCode=137)
    save(state)
elif args[0] == "cp":
    if os.environ.get("FAKE_MISSING_TRACE"):
        print("trace missing", file=sys.stderr)
        sys.exit(1)
    name = args[1].rsplit("/", 1)[-1]
    trace = '1 execve("/bin/true", ["/bin/true"], 0x0) = 0\n'
    if name == "execve.log":
        trace = '1 execve("/bin/bash", ["/bin/bash", "probe.sh"], 0x0) = 0\n'
        if not os.environ.get("FAKE_NO_QEMU"):
            option = '--version' if os.environ.get("FAKE_HELP_ONLY") else '-machine'
            trace += ('2 execve("/bin/qemu-system-fixture", ["qemu-system-fixture", "'
                      + option + '", "none"], 0x0) = 0\n')
    pathlib.Path(args[2]).write_text(trace)
elif args[0] == "rm":
    if os.environ.get("FAKE_CLEANUP_FAILURE"):
        print("synthetic cleanup failure", file=sys.stderr)
        sys.exit(1)
else:
    raise SystemExit("unexpected Docker command: " + repr(args))
"""
