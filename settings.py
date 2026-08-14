"""Loading of config.yaml, .env, and secrets."""

from __future__ import annotations

import os
from pathlib import Path

VALID_TYPES = {"feed", "github_release", "github_trending", "html"}
VALID_GATES = {"published", "new_only"}


class ConfigError(Exception):
    """Raised for any unusable configuration. Maps to exit code 1."""


def load_config(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ConfigError(
            "PyYAML is required. Install it with: pip install -r requirements.txt"
        ) from exc

    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config file {path} is not valid YAML: {exc}") from exc

    if not isinstance(config, dict):
        raise ConfigError(f"Config file {path} must contain a YAML mapping")

    sources = config.get("sources") or []
    if not isinstance(sources, list) or not sources:
        raise ConfigError("Config must declare a non-empty 'sources' list")

    seen_names: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ConfigError(f"sources[{index}] must be a mapping")
        name = source.get("name")
        if not name:
            raise ConfigError(f"sources[{index}] is missing a 'name'")
        if name in seen_names:
            raise ConfigError(f"Duplicate source name: {name!r}")
        seen_names.add(name)
        if not source.get("category"):
            raise ConfigError(f"Source {name!r} is missing a 'category'")
        source_type = source.get("type")
        if source_type not in VALID_TYPES:
            raise ConfigError(
                f"Source {name!r} has unknown type {source_type!r}; "
                f"expected one of {sorted(VALID_TYPES)}"
            )
        gate = source.get("gate", "published")
        if gate not in VALID_GATES:
            raise ConfigError(
                f"Source {name!r} has unknown gate {gate!r}; "
                f"expected one of {sorted(VALID_GATES)}"
            )

    return config


def load_env(project_root: Path) -> None:
    """Load .env into os.environ. Existing shell variables always win."""
    env_path = project_root / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def get_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Missing required environment variable {name}. "
            f"Set it in your shell or in .env (see .env.example)."
        )
    return value


def category_order(config: dict) -> list[str]:
    """Categories in the order they first appear in the source list."""
    order: list[str] = []
    for source in config["sources"]:
        category = source["category"]
        if category not in order:
            order.append(category)
    return order


def blurb_by_source(config: dict) -> dict[str, bool]:
    """Source name -> whether its blurb is shown, where the source overrides
    the global telegram.include_blurb. Most sources are better without one;
    a trending listing is useless without it."""
    default = bool(config.get("telegram", {}).get("include_blurb", False))
    return {
        s["name"]: bool(s.get("include_blurb", default)) for s in config["sources"]
    }


def category_icons(config: dict) -> dict[str, str]:
    """Category name -> icon. Absent or empty is fine; the renderer has a
    fallback, so a source introducing a new category still renders."""
    icons = config.get("categories") or {}
    return {str(k): str(v) for k, v in icons.items()}


def gate_by_source(config: dict) -> dict[str, str]:
    return {s["name"]: s.get("gate", "published") for s in config["sources"]}
