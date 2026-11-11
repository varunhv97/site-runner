"""Where the CLI keeps its API token: ~/.config/spinsite/config.json (owner-only), or SPINSITE_TOKEN / SPINSITE_URL."""

import json
import os
from pathlib import Path

DEFAULT_URL = "https://spinsite.app"


def path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "spinsite" / "config.json"


def load() -> dict:
    try:
        return json.loads(path().read_text())
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # created owner-only before the token is written, so it's never readable by others even briefly
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(p, 0o600)


def clear() -> bool:
    try:
        path().unlink()
        return True
    except FileNotFoundError:
        return False


def base_url() -> str:
    return (os.environ.get("SPINSITE_URL") or load().get("url") or DEFAULT_URL).rstrip("/")


def token() -> str | None:
    return os.environ.get("SPINSITE_TOKEN") or load().get("token")
