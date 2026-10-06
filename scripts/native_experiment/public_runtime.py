"""Run the existing fixed evbug/NE2000 assertions; no translation assistance or new tests."""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, "/dpf-platform")
from guest import run


def main():
    case, artifact, output = sys.argv[1:]
    index = json.loads(Path("/dpf-shared/public-tests/index.json").read_text())
    spec = index[case]
    with Path(artifact).open("rb") as stream:
        before = hashlib.file_digest(stream, "sha256").hexdigest()
    value = run(spec["profile"], artifact, spec["case"], output)
    value.update(case=case, artifact_sha256=before, boot_passed=value.get("qmp_connected") is True)
    Path(output, "result.json").write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(value), flush=True)
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
