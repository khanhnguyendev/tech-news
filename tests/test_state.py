import json
from datetime import datetime, timedelta, timezone

from state import State, freshness_cutoff, load_state, save_state

UTC = timezone.utc


def test_load_missing_file_returns_empty_state(tmp_path):
    state = load_state(tmp_path / "history.json")
    assert state.last_run is None
    assert state.seen == []


def test_round_trip(tmp_path):
    path = tmp_path / "history.json"
    original = State(last_run=datetime(2026, 8, 13, 1, 0, tzinfo=UTC), seen=["a", "b"])
    save_state(original, path)
    loaded = load_state(path)
    assert loaded.last_run == original.last_run
    assert loaded.seen == ["a", "b"]


def test_corrupt_file_is_preserved_as_bad(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{not json at all")
    state = load_state(path)
    assert state.seen == []
    assert state.last_run is None
    assert (tmp_path / "history.json.bad").read_text() == "{not json at all"


def test_save_trims_to_newest_entries(tmp_path):
    path = tmp_path / "history.json"
    save_state(State(last_run=None, seen=[str(i) for i in range(10)]), path, max_entries=4)
    assert load_state(path).seen == ["6", "7", "8", "9"]


def test_save_is_atomic_leaving_no_temp_files(tmp_path):
    path = tmp_path / "history.json"
    save_state(State(last_run=None, seen=["x"]), path)
    assert [p.name for p in tmp_path.iterdir()] == ["history.json"]


def test_saved_file_has_version(tmp_path):
    path = tmp_path / "history.json"
    save_state(State(last_run=None, seen=[]), path)
    assert json.loads(path.read_text())["version"] == 1


def test_cutoff_uses_last_run_minus_overlap():
    state = State(last_run=datetime(2026, 8, 13, 8, 0, tzinfo=UTC), seen=[])
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    assert freshness_cutoff(state, now, 6, 24) == datetime(2026, 8, 13, 2, 0, tzinfo=UTC)


def test_cutoff_first_run_uses_lookback():
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    cutoff = freshness_cutoff(State(last_run=None, seen=[]), now, 6, 24)
    assert cutoff == now - timedelta(hours=24)


def test_cutoff_is_always_timezone_aware():
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    assert freshness_cutoff(State(None, []), now, 6, 24).tzinfo is not None
