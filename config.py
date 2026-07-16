"""Non-sensitive local preferences for paper-fetch."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Callable

from store import atomic_write_json


CONFIG_VERSION = 1
DEFAULT_CONFIG_PATH = Path.home() / ".oa-paper-fetch" / "config.json"
DEFAULT_OUTPUT_DIR = Path.home() / "Desktop" / "Papers"
DEFAULT_PROFILE_DIR = Path.home() / ".oa-paper-fetch" / "profile"
CONFIG_KEYS = {
    "output_dir",
    "oa_delay",
    "timeout",
    "institutional",
    "browser_profile",
    "inst_delay",
    "inst_jitter",
    "max_institutional",
    "headless",
}
BUILTINS = {
    "output_dir": DEFAULT_OUTPUT_DIR,
    "oa_delay": 1.0,
    "timeout": 30,
    "institutional": False,
    "browser_profile": DEFAULT_PROFILE_DIR,
    "inst_delay": 4.0,
    "inst_jitter": 3.0,
    "max_institutional": 30,
    "headless": False,
}


class ConfigError(ValueError):
    pass


def _path_value(key: str, value, *, allow_relative: bool) -> Path:
    if not isinstance(value, (str, Path)):
        raise ConfigError(f"{key} must be a filesystem path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        if not allow_relative:
            raise ConfigError(f"{key} must be an absolute path after expanding ~")
        path = path.resolve()
    return path


def _float_value(key: str, value, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{key} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise ConfigError(f"{key} must be between {minimum:g} and {maximum:g}")
    return parsed


def _int_value(key: str, value, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if parsed != value and not (isinstance(value, str) and str(parsed) == value.strip()):
        raise ConfigError(f"{key} must be an integer")
    if not minimum <= parsed <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}")
    return parsed


def _validated_value(key: str, value, *, allow_relative_paths: bool):
    if key in {"output_dir", "browser_profile"}:
        return _path_value(key, value, allow_relative=allow_relative_paths)
    if key == "oa_delay":
        return _float_value(key, value, 0, 60)
    if key == "inst_delay":
        return _float_value(key, value, 4, 86400)
    if key == "inst_jitter":
        return _float_value(key, value, 0, 10)
    if key == "max_institutional":
        return _int_value(key, value, 1, 30)
    if key == "timeout":
        return _int_value(key, value, 5, 300)
    if key in {"institutional", "headless"}:
        if type(value) is not bool:
            raise ConfigError(f"{key} must be true or false")
        return value
    raise ConfigError(f"unsupported config key: {key}")


def load_config(
    path: Path, warn: Callable[[str], None] | None = None
) -> dict:
    path = Path(path).expanduser()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read config {path}: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"config {path} must contain a JSON object")
    version = payload.get("version", CONFIG_VERSION)
    if version != CONFIG_VERSION:
        raise ConfigError(f"config {path} has unsupported version {version!r}")
    report = warn or (lambda message: print(message, file=sys.stderr))
    result = {}
    for key, value in payload.items():
        if key == "version":
            continue
        if key not in CONFIG_KEYS:
            report(f"Ignoring unknown config key: {key}")
            continue
        validated = _validated_value(key, value, allow_relative_paths=False)
        result[key] = str(validated) if isinstance(validated, Path) else validated
    return result


def resolve_config(file_values: dict, cli_values: dict) -> dict:
    merged = dict(BUILTINS)
    merged.update({key: value for key, value in file_values.items() if key in CONFIG_KEYS})
    merged.update(
        {key: value for key, value in cli_values.items() if key in CONFIG_KEYS and value is not None}
    )
    resolved = {}
    for key in CONFIG_KEYS:
        resolved[key] = _validated_value(
            key, merged[key], allow_relative_paths=True
        )
    return resolved


def save_config(path: Path, updates: dict) -> dict:
    path = Path(path).expanduser()
    existing = load_config(path) if path.exists() else {}
    filtered = {
        key: value for key, value in updates.items() if key in CONFIG_KEYS and value is not None
    }
    merged = dict(existing)
    for key, value in filtered.items():
        validated = _validated_value(key, value, allow_relative_paths=True)
        merged[key] = str(validated) if isinstance(validated, Path) else validated
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    payload = {"version": CONFIG_VERSION, **merged}
    atomic_write_json(path, payload, mode=0o600)
    return payload
