"""Reading .env, and the typed readers every setting goes through."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# The repository: the folder this package sits in. .env, styles.json,
# assets/ and output/ are all found relative to it.
ROOT = Path(__file__).resolve().parent.parent

# Keys read from .env rather than the process environment, for --check-config.
_ENV_FROM_FILE: set[str] = set()


# ------------------------------------------------------------ environment ----

def load_env() -> None:
    """Read .env into the process environment.

    Existing process variables win, matching the previous behaviour. The keys
    that actually came from the file are recorded so --check-config can show
    where each setting was resolved from -- a stale shell variable silently
    shadowing .env is otherwise very hard to notice.
    """
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = _parse_env_value(value.strip())
        if key not in os.environ:
            _ENV_FROM_FILE.add(key)
        os.environ.setdefault(key, value)


def _parse_env_value(value: str) -> str:
    """Strip surrounding quotes, or an unquoted trailing ` # comment`."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value and value[0] in "\"'":
        closing = value.find(value[0], 1)
        if closing > 0:
            return value[1:closing]
    comment = re.search(r"\s+#", value)
    return value[: comment.start()].rstrip() if comment else value


def env_source(name: str) -> str:
    if name in _ENV_FROM_FILE:
        return ".env"
    return "environment" if os.getenv(name, "").strip() else "default"


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name}; set it in .env.")
    return value


def env_value(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be one of 1/0/true/false/yes/no/on/off, not {raw!r}.")


def positive_env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default)).strip() or str(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, not {raw!r}.") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}.")
    return value


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip() or str(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, not {raw!r}.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return value


def bounded_env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default)).strip() or str(default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, not {raw!r}.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return value


def env_choice(name: str, default: str, options: Any, message: str) -> str:
    """One of a fixed set of words, lower-cased, or `message` as the error."""
    value = env_value(name, default).lower()
    if value not in options:
        raise RuntimeError(message)
    return value


class ConfigProblems:
    """Every setting that failed to parse, collected rather than raised one at a time."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def check(self, parse: Any, fallback: Any) -> Any:
        """parse(), or `fallback` with the failure recorded."""
        try:
            return parse()
        except RuntimeError as exc:
            self.messages.append(str(exc))
            return fallback

    def or_default(self, parse: Any) -> Any:
        """`parse`, answering its own default (its second argument) when it fails."""
        def lenient(name: str, default: Any, *rest: Any, **options: Any) -> Any:
            return self.check(lambda: parse(name, default, *rest, **options), default)
        return lenient

    def raise_if_any(self) -> None:
        if len(self.messages) == 1:
            raise RuntimeError(self.messages[0])
        if self.messages:
            listed = "\n".join(f"  {number}. {message}" for number, message in enumerate(self.messages, 1))
            raise RuntimeError(f"{len(self.messages)} settings need attention:\n{listed}")


def resolve_asset_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


