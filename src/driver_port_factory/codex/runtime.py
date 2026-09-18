"""Use the active relay's environment credential without copying secrets into jobs."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path


def relay_overrides() -> list[str]:
    config = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    if not config.is_file():
        return []
    document = tomllib.loads(config.read_text())
    name = document.get("model_provider", "openai")
    provider = document.get("model_providers", {}).get(name, {})
    env_key = provider.get("env_key")
    if (
        name not in {"openai", "ollama", "lmstudio"}
        and re.fullmatch(r"[A-Za-z0-9_-]+", name)
        and provider.get("base_url")
        and env_key
        and os.environ.get(env_key)
    ):
        return ["-c", f"model_providers.{name}.requires_openai_auth=false"]
    return []
