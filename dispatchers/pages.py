"""Publish the generated site to a GitHub Pages branch.

The site is mirrored into a working copy and force-pushed as a single
commit on an orphan branch. Daily HTML would otherwise add a commit a day
to the repository forever, and none of that history is worth keeping: the
directory already holds `site.keep_days` of archive, and anything older
was deliberately pruned.

Command construction is kept separate from execution so the commands can
be asserted in tests without a git remote, matching how the video step
builds its ffmpeg arguments.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

from models import log

PAGES_BRANCH = "gh-pages"


def pages_url(repo: str) -> str:
    owner, _, name = repo.partition("/")
    return f"https://{owner}.github.io/{name}/"


def publish_commands(work_dir: Path, cfg: dict, *, message: str) -> list[list[str]]:
    """The git commands that publish `work_dir` to the pages branch.

    The working copy is rebuilt from nothing on every run, so the branch is
    always exactly one commit deep and the sequence is idempotent. An
    earlier version kept a persistent clone and ran `checkout --orphan`,
    which succeeds the first time and fails ever after with "a branch
    named gh-pages already exists" -- a bug that only appears on the
    second publish, which is to say in production rather than in testing.

    Identity is passed per-command because a freshly initialised repo
    inherits none, and this machine has no global git identity.
    """
    repo = cfg["repo"]
    work = str(work_dir)
    name = cfg.get("author_name", "TechNews")
    email = cfg.get("author_email", "technews@users.noreply.github.com")
    return [
        ["git", "-C", work, "init", "--initial-branch", PAGES_BRANCH],
        ["git", "-C", work, "add", "--all"],
        [
            "git", "-C", work,
            "-c", f"user.name={name}", "-c", f"user.email={email}",
            "commit", "--message", message,
        ],
        [
            "git", "-C", work, "push", "--force",
            f"https://github.com/{repo}.git", f"HEAD:{PAGES_BRANCH}",
        ],
    ]


def _mirror(site_dir: Path, work_dir: Path) -> None:
    """Build the working copy from scratch out of the site directory.

    Rebuilding rather than merging is what makes a pruned archive page, or
    a repo that fell off trending, actually disappear from the published
    site instead of lingering forever.
    """
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    for item in site_dir.iterdir():
        target = work_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    # GitHub Pages runs Jekyll unless told not to, and Jekyll silently
    # drops paths beginning with an underscore.
    (work_dir / ".nojekyll").write_text("", encoding="utf-8")


def publish(
    site_dir: Path,
    work_dir: Path,
    cfg: dict,
    *,
    runner=subprocess.run,
) -> str | None:
    """Mirror the site into `work_dir` and force-push it. Returns the URL."""
    if not cfg.get("enabled", False):
        return None
    site_dir = Path(site_dir)
    if not site_dir.is_dir():
        raise FileNotFoundError(f"Site directory not found: {site_dir}")

    work_dir = Path(work_dir)
    _mirror(site_dir, work_dir)

    message = f"Publish TechNews site {date.today().isoformat()}"
    for command in publish_commands(work_dir, cfg, message=message):
        result = runner(command, capture_output=True)
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(f"{' '.join(command[:4])} failed: {stderr}")

    url = pages_url(cfg["repo"])
    log.info("Site published to %s", url)
    return url
