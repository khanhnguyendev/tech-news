#!/usr/bin/env python3
"""TechNews CLI entry point.

Exit codes:
  0  success, including "nothing new today"
  1  setup error (missing secret, unusable config)
  2  Telegram delivery failed
  3  every attempted source failed
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pipeline
from collectors.http import make_session
from models import log, setup_logging
from settings import ConfigError, load_config, load_env

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


def _history_file() -> Path:
    """The history file under the current TECHNEWS_DATA_DIR.

    Deliberately re-read from os.environ on every call rather than taken
    from models.HISTORY_FILE: that constant is computed once, the first
    time models.py is imported, and never changes afterwards. In a real
    process that is harmless (the env var is set once, before Python
    starts). But it means a stale value inside a single long-lived
    process -- which is exactly what happens across a pytest session,
    where the module is imported once and many tests each want their own
    TECHNEWS_DATA_DIR. Recomputing here keeps main.py honoring whatever
    the environment says right now.
    """
    data_dir = Path(os.environ.get("TECHNEWS_DATA_DIR", str(Path.home() / ".technews")))
    return data_dir / "history.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="technews",
        description="Collect today's tech, AI, and security headlines "
        "and deliver them to Telegram.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run everything but print the digest instead of sending it; "
        "nothing is persisted",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="mark everything currently available as seen without sending; "
        "use once, before the first real run",
    )
    parser.add_argument(
        "--reset", action="store_true", help="delete the history file"
    )
    parser.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt for --reset"
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG, help="path to config.yaml"
    )
    parser.add_argument(
        "--only", metavar="NAME", help="run a single source by its config name"
    )
    parser.add_argument("--verbose", action="store_true", help="debug-level logging")
    return parser


def _reset(assume_yes: bool) -> int:
    history_file = _history_file()
    if not history_file.exists():
        print(f"No history file at {history_file}; nothing to reset.")
        return 0
    if not assume_yes:
        answer = input(f"Delete {history_file}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return 0
    history_file.unlink()
    print(f"Deleted {history_file}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    load_env(PROJECT_ROOT)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    if args.reset:
        return _reset(args.yes)

    session = make_session()
    try:
        outcome = pipeline.run(
            config,
            session=session,
            now=datetime.now(timezone.utc),
            dry_run=args.dry_run,
            init=args.init,
            only=args.only,
            state_path=_history_file(),
        )
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    log.info("Run finished with exit code %d", outcome.exit_code)
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
