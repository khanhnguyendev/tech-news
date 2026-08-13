# TechNews

A CLI that collects daily tech, AI, and cybersecurity headlines from a
declared list of sources and delivers them as a Telegram digest, unattended,
every morning.

## What it does

`main.py` runs a fixed pipeline, once per invocation:

1. **collect** — fetch every enabled source in `config.yaml` (RSS/Atom feeds,
   GitHub releases, or CSS-selector HTML scraping), isolated per source so one
   dead feed can't take the rest of the run down with it
2. **dedup** — drop anything whose normalized link is already in
   `~/.technews/history.json`
3. **freshness gate** — drop anything published before the cutoff
   (`last_run` minus a small overlap, or a lookback window on the first run);
   sources declared `gate: new_only` skip this and rely on dedup alone
4. **dispatch** — render the surviving articles into a category-grouped
   Telegram digest and send it; a failure here is the only thing that stops
   history from being persisted for the affected articles
5. **persist** — record `last_run` and the newly delivered ids, atomically

Only the Telegram digest is implemented today. `config.yaml` also carries a
`video` and a `site` section for the video-recap and static-site outputs
described in the project's design spec, but those dispatchers don't exist
yet (they're future milestones) — `main.py` reads and ignores those sections
for now.

## Requirements

- Python 3.10+
- The packages in `requirements.txt` (`requests`, `feedparser`,
  `beautifulsoup4`, `lxml`, `PyYAML`, `Pillow`)
- `ffmpeg` and the DejaVu fonts, for the not-yet-built video output —
  harmless to install now, not required for the Telegram digest to work
- A Telegram bot token and a destination chat id

## Setup

```bash
pip install -r requirements.txt
sudo apt install ffmpeg fonts-dejavu-core
cp .env.example .env      # then fill in the bot token and chat id
python3 main.py --dry-run # verify collection without sending
python3 main.py --init    # seed history so the first real run is not a flood
mkdir -p ~/.config/systemd/user
cp systemd/technews.* ~/.config/systemd/user/
systemctl --user enable --now technews.timer
```

If you're working from this repo's own checkout, dependencies live in a
virtualenv at the repo root — use `./.venv/bin/python3 main.py ...` (or
activate it) rather than the bare system `python3`, which does not have
these packages installed. The shipped `systemd/technews.service` already
points at the virtualenv interpreter for exactly this reason.

## CLI

```
technews [--dry-run] [--init] [--reset [--yes]] [--config PATH]
         [--only NAME] [--verbose]
```

| Flag | Behavior |
|---|---|
| *(none)* | Full run: collect, filter, send the digest, persist state |
| `--dry-run` | Run the full pipeline and print the digest to stdout; sends nothing, persists nothing |
| `--init` | Collect everything currently visible and mark it all as seen, ignoring the normal limits; sends nothing. Run this once before the first real run so it doesn't flood the chat |
| `--reset` | Delete `history.json`; prompts for confirmation unless `--yes` is also given |
| `--config PATH` | Use an alternate config file instead of the repo's `config.yaml` |
| `--only NAME` | Run a single source by its `name` in the config — the primary tool for diagnosing one broken source among many |
| `--verbose` | Debug-level logging |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success, including "no new articles today" |
| 1 | Setup error: missing config file, malformed `config.yaml`, unknown source type, missing PyYAML |
| 2 | Telegram delivery failed partway through; articles from messages that *did* send are still marked seen, so the next run won't re-send them |
| 3 | Every enabled source failed to collect (e.g. a total network outage) — distinct from "no new articles" so an outage doesn't look like a quiet news day |

## Configuration

Everything lives in `config.yaml`: delivery options, freshness/limit tuning,
and the `sources` list. Category order in the digest follows the order
categories first appear in `sources` — reordering the config reorders the
output.

### Adding a source

No Python is required. A plain feed needs four keys:

```yaml
  - name: "Some New Feed"
    category: "Security"
    type: feed
    url: "https://example.com/rss.xml"
```

`type: github_release` needs `repo` instead of `url` (and accepts
`include_prereleases: false`, the default, to exclude drafts and
prereleases). `type: html` needs `url` plus a `selectors` block with `item`,
`title`, and `link` CSS selectors (`date` and `blurb` are optional) for
sources that publish no feed at all.

Optional per-source keys: `enabled` (default `true` — set `false` to disable
a source without deleting it), `gate` (`published`, the default, or
`new_only` for sources whose page has no reliable per-item timestamp, such
as an events listing or a "trending" snapshot), and `keywords` (a
case-insensitive substring filter over headline + blurb; omit or leave empty
for no filtering).

### Secrets

Read from the shell environment first, then from `.env` in the project root
(shell always wins). See `.env.example`:

| Variable | Purpose |
|---|---|
| `TECHNEWS_TELEGRAM_BOT_TOKEN` | Bot token from @BotFather — required |
| `TECHNEWS_TELEGRAM_CHAT_ID` | Destination chat/group/channel id — required |
| `GITHUB_TOKEN` | Optional; raises the GitHub API rate limit for release collection |

## State and logs

Both live under `~/.technews/` by default (override with the
`TECHNEWS_DATA_DIR` environment variable):

- `~/.technews/history.json` — dedup ledger (`seen` ids) and `last_run`,
  written atomically; a corrupt file is renamed to `history.json.bad` rather
  than overwritten
- `~/.technews/app.log` — rotating log (1 MB × 3 backups), mirrored to
  stderr

## Scheduling

`systemd/technews.service` and `systemd/technews.timer` run the digest every
morning at 08:00 as a **user** timer (see the Setup section above to install
them). `Persistent=true` on the timer means a machine that was powered off
at 08:00 runs the job once it comes back up, instead of skipping that day
entirely, the way plain cron would. Because the freshness gate is based on
`last_run` rather than the calendar date, that catch-up run still produces a
complete digest of everything published since the last successful run, not
just "today".

## Testing

```bash
./.venv/bin/python -m pytest
```

No test touches the network. Live source verification is a separate,
hand-run step — see `scripts/verify_sources.py`, which fetches every
configured source once, reports whether it returned dated items, and is
never invoked from the test suite.
