import subprocess
from pathlib import Path

import pytest

from dispatchers.pages import PAGES_BRANCH, publish, publish_commands


def cfg(**overrides):
    base = {"enabled": True, "repo": "khanhnguyendev/tech-news"}
    base.update(overrides)
    return base


def test_commands_force_push_a_single_commit_to_the_pages_branch():
    """Force-pushing one commit keeps a year of daily HTML out of the
    repository's history. The 30 days of archive live in the directory
    itself, so git history adds nothing that would be missed."""
    commands = publish_commands(Path("/work"), cfg(), message="x")
    joined = [" ".join(c) for c in commands]
    assert any("init" in c and "--initial-branch" in c for c in commands)
    assert any("--force" in c and f"HEAD:{PAGES_BRANCH}" in c for c in commands)
    assert not any("--orphan" in c for c in commands), (
        "checkout --orphan only works on a branch that does not exist yet, "
        "so it fails on every publish after the first"
    )


def test_commands_stay_inside_the_worktree():
    commands = publish_commands(Path("/work"), cfg(), message="x")
    for command in commands:
        if command[0] == "git":
            assert command[1] == "-C" and command[2] == "/work", command


def test_nothing_runs_when_publishing_is_disabled(tmp_path):
    calls = []
    result = publish(tmp_path, tmp_path / "w", cfg(enabled=False),
                     runner=lambda *a, **k: calls.append(a))
    assert result is None
    assert calls == []


def test_missing_site_directory_is_reported_not_published(tmp_path):
    with pytest.raises(FileNotFoundError):
        publish(tmp_path / "absent", tmp_path / "w", cfg(), runner=lambda *a, **k: None)


def test_publish_returns_the_pages_url(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html></html>")

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, b"", b"")

    url = publish(site, tmp_path / "work", cfg(), runner=runner)
    assert url == "https://khanhnguyendev.github.io/tech-news/"


def test_a_failing_git_command_raises_with_its_stderr(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html></html>")

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, b"", b"remote rejected")

    with pytest.raises(RuntimeError, match="remote rejected"):
        publish(site, tmp_path / "work", cfg(), runner=runner)


def test_a_nojekyll_file_is_written(tmp_path):
    """GitHub Pages runs Jekyll by default, which silently drops files and
    directories beginning with an underscore. The site has none today, but
    the failure would be invisible if it ever did."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html></html>")
    work = tmp_path / "work"

    publish(site, work, cfg(), runner=lambda c, **k: subprocess.CompletedProcess(c, 0, b"", b""))
    assert (work / ".nojekyll").exists()


def test_the_site_is_mirrored_not_merged(tmp_path):
    """A repo removed from trending, or an archive page pruned past
    keep_days, must disappear from the published site too."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html>new</html>")
    work = tmp_path / "work"
    work.mkdir()
    stale = work / "2020-01-01.html"
    stale.write_text("old")

    publish(site, work, cfg(), runner=lambda c, **k: subprocess.CompletedProcess(c, 0, b"", b""))

    assert not stale.exists(), "stale pages must not survive a publish"
    assert (work / "index.html").read_text() == "<html>new</html>"
