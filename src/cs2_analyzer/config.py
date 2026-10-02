"""Configuration loading.

All tunable values live in ``default_config.toml`` (shipped with the package)
and can be overridden by a user TOML file (``--config``) or environment
variables for infrastructure settings.

Thresholds marked ``UNCALIBRATED`` in the TOML are development placeholders.
They were NOT derived from population data and must be replaced by values from
the calibration tooling (``tools/calibration``) before results are trusted.
"""

from __future__ import annotations

import copy
import os
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

_MISSING = object()


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class ConfigError(ValueError):
    """A setting is missing or invalid; the CLI prints the message without a traceback."""


def load_dotenv(path: str | Path = ".env") -> list[str]:
    """Set ``KEY=value`` lines from a ``.env`` file as environment variables.

    Variables that are already set win, so the shell (and Docker) can still override
    the file. Returns the names that were set.
    """
    p = Path(path)
    if not p.is_file():
        return []
    loaded = []
    for line in p.read_text("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


class Config:
    """Nested configuration with dotted-path access (``cfg.get("a.b.c")``)."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    @classmethod
    def load(cls, path: str | Path | None = None, overrides: dict | None = None) -> "Config":
        text = resources.files("cs2_analyzer").joinpath("default_config.toml").read_text("utf-8")
        data = tomllib.loads(text)
        if path:
            with open(path, "rb") as fh:
                data = _deep_merge(data, tomllib.load(fh))
        if overrides:
            data = _deep_merge(data, overrides)
        # Infrastructure settings may come from the environment (Docker).
        if os.environ.get("CS2A_DATABASE_URL"):
            data["storage"]["database_url"] = os.environ["CS2A_DATABASE_URL"]
        if os.environ.get("CS2A_MAPS_DIR"):
            data["geometry"]["maps_dir"] = os.environ["CS2A_MAPS_DIR"]
        if os.environ.get("CS2A_OUTPUT_DIR"):
            data["output"]["dir"] = os.environ["CS2A_OUTPUT_DIR"]
        if os.environ.get("CS2A_AUTH_ENABLED"):
            data["auth"]["enabled"] = os.environ["CS2A_AUTH_ENABLED"].strip().lower() in ("1", "true", "yes")
        if os.environ.get("CS2A_PUBLIC_URL"):
            data["auth"]["public_url"] = os.environ["CS2A_PUBLIC_URL"]
        if os.environ.get("CS2A_REQUIRE_MATCH_ACCESS"):
            data["ingest"]["require_match_access"] = os.environ["CS2A_REQUIRE_MATCH_ACCESS"].strip().lower() in ("1", "true", "yes")
        if os.environ.get("CS2A_SERVICE_TOKEN"):
            data["ingest"]["service_token"] = os.environ["CS2A_SERVICE_TOKEN"]
        if os.environ.get("CS2A_CONTACT_EMAIL"):
            data.setdefault("site", {})["contact_email"] = os.environ["CS2A_CONTACT_EMAIL"]
        for env, key in (("CS2A_MAX_UPLOAD_MB", "max_upload_mb"), ("CS2A_MAX_PARALLEL_UPLOADS", "max_parallel_uploads"),
                         ("CS2A_MAX_UPLOADS_PER_DAY", "max_uploads_per_day"), ("CS2A_MAX_QUEUED_UPLOADS", "max_queued_uploads")):
            if os.environ.get(env):
                data["api"][key] = int(os.environ[env])
        if os.environ.get("CS2A_API_WORKERS"):
            data["api"]["workers"] = int(os.environ["CS2A_API_WORKERS"])
        if os.environ.get("CS2A_MIN_FREE_DISK_GB"):
            data["api"]["min_free_disk_gb"] = float(os.environ["CS2A_MIN_FREE_DISK_GB"])
        if os.environ.get("CS2A_ADMIN_STEAM_IDS"):
            data["auth"]["admin_steam_ids"] = [x for x in os.environ["CS2A_ADMIN_STEAM_IDS"].replace(";", ",").replace(" ", ",").split(",") if x]
        if os.environ.get("CS2A_STEAM_API_KEY"):
            data["auth"]["steam_api_key"] = os.environ["CS2A_STEAM_API_KEY"]
        return cls(data)

    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                if default is _MISSING:
                    raise KeyError(f"config key not found: {dotted}")
                return default
        return node

    def section(self, dotted: str) -> dict[str, Any]:
        value = self.get(dotted, {})
        return dict(value) if isinstance(value, dict) else {}

    def with_overrides(self, overrides: dict) -> "Config":
        return Config(_deep_merge(self._data, overrides))

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)
