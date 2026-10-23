"""Tiny stdlib .env loader and typed env helpers.

Only reads the file if it exists and never overrides variables that are
already set in the real environment.
"""

import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace("\\n", "\n").replace('\\"', '"')
        elif " #" in value:
            # Inline comment on an unquoted value.
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env_str(name, default).split(",") if item.strip()]


def require_env(names: list[str]) -> None:
    missing = [name for name in names if not env_str(name)]
    if missing:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill them in "
            "(or set them in the real environment)."
        )


def env_hour_range(name: str, default: str) -> tuple[int, int]:
    """Parse "9-18" into (9, 18): local hours [start, end) when sending is allowed."""
    raw = env_str(name, default)
    try:
        start, end = (int(part) for part in raw.split("-"))
    except ValueError:
        start = end = -1
    if not 0 <= start < end <= 24:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(f"{name} must look like 9-18 (hours 0-24, start < end), got {raw!r}.")
    return start, end
