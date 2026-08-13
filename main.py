#!/usr/bin/env python3
"""TechNews CLI entry point.

Exit codes:
  0  success, including "nothing new today"
  1  setup error (missing secret, unusable config, PyYAML missing)
  2  Telegram delivery failed
  3  every attempted source failed
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pipeline
from collectors.http import make_session
from dispatchers import make_extras
from models import data_dir, history_file, log, setup_logging
from settings import ConfigError, get_secret, load_config, load_env

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


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
    path = history_file()
    if not path.exists():
        print(f"No history file at {path}; nothing to reset.")
        return 0
    if not assume_yes:
        answer = input(f"Delete {path}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return 0
    path.unlink()
    print(f"Deleted {path}.")
    print(
        "Run 'python3 main.py --init' before your next real run, or "
        "every item currently visible across all sources will be sent at once."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # load_env() first: a TECHNEWS_DATA_DIR set only in .env (not the shell)
    # must be in os.environ before setup_logging() resolves where app.log
    # goes, otherwise the log lands in a different directory than
    # history.json and the video output.
    load_env(PROJECT_ROOT)
    setup_logging(args.verbose)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    if args.reset:
        return _reset(args.yes)

    session = make_session()
    now = datetime.now(timezone.utc)

    extras_fn = None
    if not args.dry_run and not args.init:
        try:
            extras_fn = make_extras(
                session,
                config,
                day=now.date(),
                token=get_secret("TECHNEWS_TELEGRAM_BOT_TOKEN"),
                chat_id=get_secret("TECHNEWS_TELEGRAM_CHAT_ID"),
                data_dir=data_dir(),
            )
        except ConfigError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 1

    try:
        outcome = pipeline.run(
            config,
            session=session,
            now=now,
            dry_run=args.dry_run,
            init=args.init,
            only=args.only,
            state_path=history_file(),
            extras_fn=extras_fn,
        )
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Invalid argument: {exc}", file=sys.stderr)
        return 1

    log.info("Run finished with exit code %d", outcome.exit_code)
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
