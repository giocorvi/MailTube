from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_PATH = Path(".mailtube.env")
_env_loaded = False


def load_local_env(*, force: bool = False) -> None:
    global _env_loaded
    if _env_loaded and not force:
        return

    path = Path(os.getenv("MAIL_TUBE_ENV_FILE", str(DEFAULT_ENV_PATH)))
    _env_loaded = True
    if not path.exists():
        return

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] in {"'", '"'} and value[-1:] == value[0]:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value


def get_youtube_api_key() -> str | None:
    load_local_env()
    key = os.getenv("YOUTUBE_API_KEY")
    if key:
        return key
    load_local_env(force=True)
    return os.getenv("YOUTUBE_API_KEY")
