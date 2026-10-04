"""Explain captured environment failure without relaxing the acceptance decision."""


def failures(command, qemu_programs, container, collector):
    reasons = []
    if not command.launched or command.launch_error:
        reasons.append(f"Harness could not launch: {command.launch_error}")
    if command.timed_out:
        reasons.append("Harness exceeded its time limit; timeout is not a passing device assertion")
    elif command.exit_code:
        reasons.append(f"Harness exited {command.exit_code}; inspect stderr and device assertions")
    if not collector.get("available"):
        reasons.append("Exec collector unavailable: " + str(collector.get("limitation")))
    if not container["satisfied"] or not qemu_programs:
        reasons.extend(container.get("observation_errors", []))
    if not container["satisfied"]:
        reasons.append(
            "Required official container execution was not established. Use the current workspace "
            "bind mount and keep target QEMU inside the selected official image."
        )
    if not qemu_programs:
        reasons.append(
            "No attributable QEMU experiment was captured; --version/--help is discovery only. "
            "Check the container/exec observations before investigating driver behavior."
        )
    return reasons


def message(reasons, attempt, command):
    # Full, possibly repeated collector details are retained in the attempt.
    details = "\n".join("- " + str(reason)[:1200] for reason in reasons[:6])
    return (
        f"Environment smoke failed:\n{details}\n"
        f"Evidence: {attempt}\nstdout: {command.stdout_path}\nstderr: {command.stderr_path}"
    )


def platform_failures(project, container):
    from ..platform.service import route_errors

    return route_errors(project, container)
