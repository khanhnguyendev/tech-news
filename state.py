"""The dedup ledger and the freshness cutoff derived from it."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from models import HISTORY_FILE, log

STATE_VERSION = 1


@dataclass
class State:
    last_run: datetime | None = None
    seen: list[str] = field(default_factory=list)


def load_state(path: Path = HISTORY_FILE) -> State:
    if not path.is_file():
        return State()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        seen = [str(item) for item in raw.get("seen", [])]
        last_run_raw = raw.get("last_run")
        last_run = _parse_iso(last_run_raw) if last_run_raw else None
    except (json.JSONDecodeError, ValueError, AttributeError, TypeError) as exc:
        bad_path = path.with_suffix(path.suffix + ".bad")
        path.replace(bad_path)
        log.error("History file was unreadable (%s); preserved at %s", exc, bad_path)
        return State()
    return State(last_run=last_run, seen=seen)


def save_state(
    state: State, path: Path = HISTORY_FILE, max_entries: int = 800
) -> None:
    """Write the ledger atomically, keeping only the newest max_entries ids."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STATE_VERSION,
        "last_run": state.last_run.isoformat() if state.last_run else None,
        "seen": state.seen[-max_entries:],
    }
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def freshness_cutoff(
    state: State,
    now: datetime,
    overlap_hours: int,
    first_run_lookback_hours: int,
) -> datetime:
    """Articles published before this instant are stale.

    On a normal run the cutoff is last_run minus an overlap, so nothing
    published between runs can slip through. History dedup makes the
    overlap free.
    """
    if state.last_run is None:
        return now - timedelta(hours=first_run_lookback_hours)
    return state.last_run - timedelta(hours=overlap_hours)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
