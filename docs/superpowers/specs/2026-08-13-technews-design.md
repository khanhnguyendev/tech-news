# TechNews — Design Spec

**Date:** 2026-08-13
**Status:** Approved
**Repo:** khanhnguyendev/tech-news
**Runtime:** Python 3.10+ (developed against 3.12.3)

A CLI pipeline that collects daily tech, AI, and cybersecurity headlines from
declared sources and delivers them as a Telegram digest, plus a video recap and
a static HTML page.

This spec supersedes the original PRD where the two disagree. Every deviation is
marked **Deviation from PRD** with its reason.

---

## 1. Goals and non-goals

### Goals

- Collect from ~11 source groups spanning RSS/Atom feeds, GitHub releases, and
  HTML pages.
- Normalize every item into a single `Article` type.
- Never report the same item twice; never silently lose an item.
- Deliver a category-grouped digest to Telegram every morning, unattended.
- Produce a silent slideshow video recap with background music and a timeline
  script, delivered to the same Telegram chat.
- Produce a self-contained static HTML page with a rolling archive.
- Keep all source declarations, toggles, and filters in one `config.yaml`.

### Non-goals

- Streaming or push-based alerting. This is a scheduled batch job.
- Full-text storage, indexing, or search. It links to originals.
- Multi-user auth, admin panels, hosted dashboards.
- AI summarization, translation, or relevance scoring. Items are forwarded as
  received.
- Narration / text-to-speech. The video is silent by design; the timeline
  artifact exists so narration can be added later without re-running collection.

### Definition of done

- The digest lands every morning with zero manual effort.
- Over any rolling 30-day window, no major announcement from a tracked source
  goes unreported.
- Adding a new RSS source requires editing `config.yaml` only — no Python.
- After first-time setup, the system runs hands-free indefinitely.

---

## 2. Architecture

Pipeline stages: **collect → dedup → freshness gate → dispatch → persist**.

```
config.yaml
    │
    ▼
  main.py            CLI flag parsing only
    │
    ▼
  pipeline.py ──────▶ collectors/  (registry: type → strategy)
    │                   feed.py · github_release.py · html_scrape.py
    │
    ▼
  per-source keyword filter (only where `keywords` is declared)
    │
    ▼
  drop ids already in state.seen
    │
    ▼
  freshness gate (per-source: published | new_only)
    │
    ▼
  limits: max_per_source, max_total
    │
    ├──▶ dispatchers/telegram.py   CRITICAL — text digest
    ├──▶ dispatchers/video.py      non-critical — mp4 + timeline, sent via sendVideo
    └──▶ dispatchers/site.py       non-critical — index.html + archive
    │
    ▼
  state.py: persist seen + last_run (atomic write)
```

### File layout

```
tech-news/
  main.py                  CLI entry point (~60 lines): flags → pipeline call → exit code
  pipeline.py              orchestration: collect → dedup → gate → limits → dispatch → persist
  state.py                 history.json I/O, URL normalization, freshness cutoff
  models.py                Article dataclass, path constants, logging setup
  settings.py              config.yaml (PyYAML) + .env loading
  collectors/
    __init__.py            strategy registry, collect_all()
    http.py                shared requests.Session: UA, timeout, retry
    feed.py                strategy "feed"           (feedparser)
    github_release.py      strategy "github_release" (GitHub REST API)
    html_scrape.py         strategy "html"           (bs4 + lxml)
  dispatchers/
    __init__.py
    telegram.py
    video.py
    site.py
  tests/
    fixtures/              real saved XML/HTML/JSON — no test touches the network
    ...
  config.yaml
  .env.example
  requirements.txt
  systemd/technews.service technews.timer
  README.md
```

**Deviation from PRD:** the PRD gives `main.py` both flag parsing and full
orchestration, and folds history handling into it. Orchestration moves to
`pipeline.py` so the whole pipeline is callable as a function in tests without
simulating `sys.argv` or catching `SystemExit`. History handling moves to
`state.py` because the dedup ledger and `last_run` are now coupled (the
freshness gate depends on `last_run`), and the "Telegram failed → do not persist"
rule is the system's most important invariant — it deserves its own module and
its own tests.

**Deviation from PRD:** the seven hand-written collector modules
(`anthropic.py`, `youtube.py`, `electron.py`, `apple.py`, `playwright.py`,
`github_trending.py`, `security.py`) are replaced by three generic strategies
plus a declarative source list. Of the 11 source groups, roughly nine are plain
RSS/Atom feeds differing only by URL and category label; hand-writing them
produces four near-identical modules and four near-identical test suites.
Adding an RSS source becomes three lines of YAML and zero lines of Python,
which beats the "under 30 minutes" DoD target by a wide margin. The plugin
boundary survives: a genuinely deviant source can still ship as its own module
registered under a new `type`.

---

## 3. Data model

```python
@dataclass(frozen=True, slots=True)
class Article:
    category: str                 # grouping key → digest section header
    source: str                   # specific source name, e.g. "Krebs on Security"
    headline: str
    link: str
    published: datetime | None    # ALWAYS timezone-aware, converted to UTC
    blurb: str = ""

    @property
    def id(self) -> str:          # normalized link — the sole dedup key
        ...
```

**Deviation from PRD:** `date: str` becomes `published: datetime | None`,
timezone-aware in UTC. String dates cannot support the chosen freshness gate —
comparing an RFC 822 string against an RFC 3339 string is meaningless. The
conversion happens inside the collector so every downstream layer sees exactly
one type. Sources whose date cannot be parsed yield `published = None`.

**Deviation from PRD:** `source` is added alongside `category`. The declarative
model groups many sources under one category (six publications under
`Security`), so the digest still needs to name the individual publication.

### URL normalization (`Article.id`)

Applied in order:

1. Strip the fragment (`#...`).
2. Drop query parameters whose name matches `utm_*`, plus `fbclid` and `gclid`.
3. Strip a single trailing `/` from the path.
4. Lowercase the scheme and host. **Leave the path case untouched** — many sites
   treat paths case-sensitively.

This is the only dedup identifier. It must be pure and deterministic.

---

## 4. Configuration

### `config.yaml`

```yaml
telegram:
  include_blurb: false
  disable_web_page_preview: true
  send_when_empty: false

freshness:
  overlap_hours: 6
  first_run_lookback_hours: 24

limits:
  max_per_source: 10
  max_total: 60

history:
  max_entries: 800

video:
  enabled: true
  seconds_per_slide: 4
  max_slides: 20
  resolution: [1080, 1920]
  font: /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
  music: assets/music.mp3        # optional; absent → silent video
  send_to_telegram: true

site:
  enabled: true
  output_dir: ~/.technews/site
  keep_days: 30

sources:
  - name: "Anthropic News"
    category: "Anthropic"
    type: feed
    url: "https://www.anthropic.com/news/rss.xml"

  - name: "Anthropic Events"
    category: "Anthropic"
    type: html
    url: "https://www.anthropic.com/events"
    gate: new_only
    selectors:                    # TO BE FILLED during implementation by
      item: "<css>"               # inspecting the live page; a saved copy of
      title: "<css>"              # that page becomes the test fixture
      link: "<css>"
      date: "<css>"

  - name: "Fireship"
    category: "YouTube"
    type: feed
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=..."

  - name: "Playwright"
    category: "Releases"
    type: github_release
    repo: "microsoft/playwright"

  - name: "The Hacker News"
    category: "Security"
    type: feed
    url: "https://feeds.feedburner.com/TheHackersNews"
    keywords: []                  # optional; empty/absent = no filtering
```

Per-source keys: `name`, `category`, `type`, `enabled` (default `true`),
`gate` (default `published`), `keywords` (optional), plus type-specific keys
(`url`, `repo`, `selectors`, `include_prereleases`).

**Category ordering in all outputs follows the order categories first appear in
`sources`.** Reordering the config reorders the digest. No separate ordering key.

**Deviation from PRD:** the bundled mini-YAML parser is dropped. `settings.py`
requires PyYAML and fails with exit code 1 and an explicit install command if it
is missing. The fallback parser is ~150 lines of code plus tests to avoid a
one-line `pip install`, and PyYAML 6.0.1 is already present on the target
machine.

### Secrets

Loaded from the shell environment first, then `.env` in the project root. Shell
always wins.

| Variable | Purpose |
|---|---|
| `TECHNEWS_TELEGRAM_BOT_TOKEN` | Bot token from @BotFather — required |
| `TECHNEWS_TELEGRAM_CHAT_ID` | Destination chat/group/channel id — required |
| `GITHUB_TOKEN` | Optional; raises the GitHub API rate limit |

`.env` is listed in `.gitignore`; `.env.example` is committed.

---

## 5. Collection

### Registry and isolation

`collectors/__init__.py` holds a `type → strategy` dict and exposes
`collect_all(config, session)`. It iterates `sources`, skips `enabled: false`
(logged), and **wraps each source in its own try/except**. One dead feed cannot
take down the other ten; the log records the source name and the exception.
This invariant lives in exactly one place.

### `http.py`

A shared `requests.Session`: custom User-Agent, 20-second timeout, and exactly
one retry with short backoff on connection errors and 5xx. `feedparser` does
**not** fetch anything itself — bytes are fetched through this session and
handed to it, so UA, timeout, and retry are controlled in one place.

The session is injected into `collect_all` and into the dispatchers rather than
constructed internally, so tests substitute a fake without monkeypatching.

### `feed.py` (type: `feed`)

Maps each feedparser entry to an `Article`:

- `headline` ← `entry.title`
- `link` ← `entry.link`
- `published` ← `entry.published_parsed`, falling back to `entry.updated_parsed`;
  feedparser normalizes to UTC, so this is wrapped into a timezone-aware
  `datetime`. Neither present → `None`.
- `blurb` ← `entry.summary` with HTML tags stripped, truncated to ~200 chars.

Malformed feeds: feedparser sets `bozo=1` but usually still yields entries.
**If entries exist, use them and log the `bozo_exception` as a warning. Only
zero entries counts as a failure.**

Covers: Anthropic news / engineering / changelog, Anthropic course-repo GitHub
Atom commits, all YouTube channels, Electron blog, Apple Developer News,
Swift.org, all security publications, and the GitHub Trending RSS mirror.

### `github_release.py` (type: `github_release`)

Calls `https://api.github.com/repos/{repo}/releases`. Unauthenticated is
sufficient (60 requests/hour versus roughly 3 used per day); `GITHUB_TOKEN` is
used when set.

- `headline` ← release `name`, falling back to `tag_name`
- `link` ← `html_url`
- `published` ← `published_at` (RFC 3339)
- `blurb` ← first ~200 chars of `body`

**Drafts and prereleases are excluded by default** (`include_prereleases: false`).
Electron publishes nightlies and alphas continuously; without this filter it
alone would fill the digest.

### `html_scrape.py` (type: `html`)

`requests` + bs4/lxml with CSS selectors from config. Dates are read
preferentially from a `<time datetime="...">` attribute (ISO 8601, parsed with
`datetime.fromisoformat`). No machine-readable date → `published = None`.

Covers: Anthropic events, and GitHub Trending if the community RSS mirror dies.

---

## 6. Deduplication, freshness, and limits

### State file

`~/.technews/history.json`:

```json
{
  "version": 1,
  "last_run": "2026-08-13T01:00:00Z",
  "seen": ["https://...", "https://..."]
}
```

Rules:

1. **`last_run` records the moment the run *started*, not finished.** Writing the
   finish time opens a gap in which items published mid-run fall between two
   runs. The 6-hour overlap would mask this, but starting-time is correct on its
   own.
2. **Writes are atomic** — write to a temp file in the same directory, then
   `os.replace()`. A crash mid-write cannot corrupt the ledger.
3. **A corrupt or unreadable file is renamed to `history.json.bad`** and an empty
   state is initialized, rather than being silently overwritten.
4. `seen` is trimmed to the newest `history.max_entries` (default 800) using
   insertion order, oldest evicted first.

**Invariant: history capacity must exceed the freshness window.** If a URL is
evicted while still inside the window, it will be re-sent. At roughly 40 items
per day, 800 entries is about 20 days versus a 6-hour window — a wide margin.
Anyone changing either number must re-check this relationship.

### Freshness gate

**Deviation from PRD — this is the most significant correctness change.**

The PRD's `keep_today()` compares against the local calendar date. With an 08:00
cron, anything published between yesterday's run and local midnight is new (not
in history) but not "today", so it is dropped permanently and never reappears.
For US-timezone sources that is most of their publishing day, and it breaks the
"no major announcement goes unreported over 30 days" criterion on day one.

Replacement:

- Cutoff = `last_run - freshness.overlap_hours` (default 6h). History dedup
  prevents repeats, so the overlap is free insurance against clock skew and
  feeds that revise timestamps after publication.
- No `last_run` (first run) → cutoff = `now - freshness.first_run_lookback_hours`
  (default 24h).
- `published is None` → dropped. Silence beats resurfacing stale content.
- Per-source `gate: new_only` → **skip the time gate entirely**, relying on
  history dedup alone.

`gate: new_only` exists for Anthropic Events: an events page lists *upcoming*
events, whose dates are event dates in the future, not publication dates. A
time-based gate either drops them all or holds them forever. Dedup alone is the
correct gate there — a new event appears once and is reported once.

### Stage order

Fixed and total:

1. **collect** — per source, isolated
2. **keyword filter** — inside `collect_all`, immediately after a source returns,
   case-insensitive substring match against `headline` + `blurb`, applied only to
   sources that declare a non-empty `keywords` list
3. **dedup** — drop ids present in `state.seen`
4. **freshness gate** — per-source `published` or `new_only`
5. **limits** — `max_per_source`, then `max_total`

### Sort order

One rule used everywhere (digest, video, site, and limit truncation): **sort by
`published` descending, with `published = None` last, then by `source` name and
`headline` as tie-breakers** so runs are deterministic. `None` can only reach
this point via `gate: new_only`.

### Limits

`max_per_source` (default 10, newest kept) then `max_total` (default 60).
**Every truncation logs how many items were dropped and from which source.**
Silent truncation would read as full coverage when it is not.

`--init` ignores the limits entirely — it must seed every currently visible id,
otherwise the first real run floods the chat with whatever the limits trimmed.

---

## 7. Dispatch

Order of operations: render digest → send text → build video → send video →
build site → persist state. **Only the text send is critical.**

When the pipeline yields zero articles and `telegram.send_when_empty` is false,
**all three dispatchers are skipped** — no message, no video, no site rewrite
(yesterday's `index.html` stays in place). `last_run` is still persisted, so the
next run's window starts from today. Exit code 0.

### `telegram.py`

Digest grouped by `category` in config order. One line per item:

```
<b>Security</b>
• <a href="...">Critical RCE in ...</a> — <i>Krebs on Security</i>
```

`blurb` is omitted from Telegram by default. At 40–60 items, two extra lines
each turns the digest into a wall of text and destroys its glanceability. The
full blurb appears on the static site. `telegram.include_blurb: true` overrides.

Requirements:

- **HTML-escape `&`, `<`, `>` in all text.** Headlines containing these are
  routine in security feeds and GitHub releases; unescaped, Telegram returns
  400 and the entire digest is lost for that day.
- `disable_web_page_preview: true` — otherwise Telegram attaches a large preview
  card for the first link and pushes everything else off-screen.
- **Split at category boundaries, never mid-tag.** If a single category exceeds
  4096 characters, split further at item boundaries.
- On HTTP 429, honor Telegram's `retry_after` value.

**Deviation from PRD — partial-delivery accounting.** The PRD says a Telegram
failure means exit 2 and no history write. That is correct for a single message,
but a digest is routinely split into 2–4 messages. If message 1 succeeds and
message 2 fails, "persist nothing" makes the next run re-send everything,
delivering duplicates of what was already read. Instead: track which articles
belong to which message, **mark articles seen per successfully sent message**,
and still exit 2. Delivered work stays delivered; undelivered work retries.

### `video.py`

Silent slideshow with background music, plus a timeline script.

Pillow renders each slide as a PNG (default 1080×1920 vertical, which plays well
on a phone inside Telegram):

- Slide 1: cover — `TechNews — 13 Aug 2026 · 42 stories`.
- Slides 2..N: one headline each — category chip, headline wrapped to the frame
  width measured via `textlength`, and the source name.
- `max_slides` (default 20) keeps the newest items; the dropped count is logged.

ffmpeg assembles them via the concat demuxer with a per-image `duration`,
encoding H.264 `yuv420p` with `+faststart`. Background music comes from
`video.music`, trimmed to video length with a fade-out. No music file → silent
video, not an error.

Timeline artifacts written alongside the mp4:

- `recap.json` — one object per segment: `index`, `start`, `end`, `category`,
  `source`, `headline`, `link`.
- `recap.srt` — the same content as subtitles, usable as a narration script.

**Testability requirement: timeline construction and ffmpeg argument
construction are pure functions, separate from execution.** Tests assert the
timeline and the argument list without invoking ffmpeg. One optional smoke test
runs the real binary and skips itself when ffmpeg is absent.

Missing ffmpeg at runtime is logged and the video step is skipped; the run does
not fail. The finished mp4 is sent with `sendVideo` to the same chat after the
text digest. A failed video send is non-critical.

### `site.py`

A single self-contained HTML file — inline CSS, no external assets, light/dark
via `prefers-color-scheme`. Blurbs are shown in full here.

Outputs under `site.output_dir` (default `~/.technews/site`):

- `index.html` — today's digest
- `archive/YYYY-MM-DD.html` — one file per day
- an archive index listing available days

Files older than `site.keep_days` (default 30) are pruned. Templating uses
`string.Template` from the standard library — one template does not justify a
Jinja dependency. The same HTML escaping rules as Telegram apply.

---

## 8. CLI, exit codes, and logging

| Flag | Behavior |
|---|---|
| *(none)* | Full run |
| `--dry-run` | Run the full pipeline, print the digest to stdout, send nothing, persist nothing |
| `--init` | Collect everything currently visible, load all ids into `seen`, set `last_run`, send nothing |
| `--reset` | Delete `history.json`; prompts for confirmation unless `--yes` is given |
| `--config PATH` | Use an alternate config file |
| `--only NAME` | Run a single source by name |

**Deviation from PRD:** `--config` and `--only` are added. `--config` is needed
for testing and for trialing a new source; `--only` is the primary diagnostic
tool when one of eleven sources breaks.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success, including "no new articles" |
| 1 | Setup error: missing token, malformed `config.yaml`, PyYAML missing |
| 2 | Telegram delivery failed (state persisted per successfully sent message) |
| 3 | **Every enabled source failed** |

**Deviation from PRD:** codes 1 and 3 are added. Code 3 matters more than it
looks: with a total network outage the pipeline produces zero articles, which is
indistinguishable from a quiet news day. Without a distinct code the system can
die silently for days while looking healthy.

Related: when there are no new articles, **nothing is sent** by default
(`telegram.send_when_empty: false`). A daily "nothing today" message only trains
the reader to ignore the bot.

### Logging

`~/.technews/app.log` via `RotatingFileHandler` (1 MB × 3), mirrored to stderr.

**Deviation from PRD:** the PRD specifies an unbounded append-only log. Running
daily and indefinitely, that becomes the largest file in `~/.technews/`.

---

## 9. Testing strategy

TDD with pytest. **No test touches the network.** `tests/fixtures/` holds real
saved payloads: an Anthropic feed, a YouTube feed, a security feed, GitHub
releases JSON, a malformed-but-parseable feed, and an events HTML page.

Coverage in risk order:

1. **The critical pipeline invariant.** With fake collectors and fake
   dispatchers: force Telegram to raise, assert `history.json` is unchanged.
   Force the *second* message to fail, assert only articles from the first
   message are marked seen.
2. **Timezone normalization.** Three fixtures — one with `Z`, one with `-0500`,
   one with a textual `EST` — must all resolve to the same UTC instant. This is
   the likeliest place to be wrong, and being wrong loses articles.
3. **Freshness-gate boundaries.** An article exactly at the cutoff, an article
   with `published = None`, a source with `gate: new_only`.
4. **`state.py`.** Round-trip, corrupt file → `.bad` + empty state, trimming at
   the cap, atomic write, `last_run` uses run-start time.
5. **Telegram rendering.** Escaping of `&`/`<`/`>`, splitting when the digest is
   exactly 4096 characters, never splitting inside a tag.
6. **URL normalization.** `utm_*` stripping, trailing slash, host lowercasing,
   path case preserved.
7. **Video.** Segment durations sum to total length, `.srt` formatting, ffmpeg
   argument list. No ffmpeg invocation, plus one self-skipping smoke test.
8. **GitHub releases.** Draft and prerelease filtering.
9. **Limits.** Correct truncation *and* that the dropped count is logged.

HTTP is injected, not monkeypatched: `pipeline` and the collectors receive a
session from the caller, so tests pass a fake that returns fixtures.

---

## 10. Dependencies and operations

**Python packages:** `requests`, `feedparser`, `beautifulsoup4`, `lxml`,
`PyYAML`, `Pillow`. Dev: `pytest`.

**System packages:** `ffmpeg`, DejaVu fonts —
`sudo apt install ffmpeg fonts-dejavu-core`.

Neither ffmpeg, nor `beautifulsoup4`/`lxml`, nor `Pillow` is present on the
target machine today; `PyYAML 6.0.1` and `requests 2.31.0` are.

**Deviation from PRD:** `feedparser` is added. Date normalization across RSS 2.0
(RFC 822), Atom (RFC 3339), textual timezones, and missing timezones is the
project's highest-risk code, and the chosen freshness gate makes a parsing error
cost either a lost article or a duplicate. feedparser has handled exactly this
for two decades and also tolerates malformed XML. `bs4` and `lxml` remain for
HTML scraping.

### Scheduling

A systemd **user** timer, not cron:

```
OnCalendar=*-*-* 08:00:00
Persistent=true
```

`Persistent=true` runs a missed job once the machine comes back up, which plain
cron cannot do. Combined with the `last_run`-based gate, a machine that was off
for three days still produces a complete catch-up digest.

### First-time setup

1. `pip install -r requirements.txt`
2. `sudo apt install ffmpeg fonts-dejavu-core`
3. Copy `.env.example` to `.env` and fill in the bot token and chat id
4. `python3 main.py --dry-run` to verify collection
5. `python3 main.py --init` to seed history without flooding the chat
6. `systemctl --user enable --now technews.timer`

---

## 11. Risk register

| Risk | Mitigation |
|---|---|
| An upstream feed changes schema or goes offline | Per-source try/except; the log names the failing source; `--only NAME` reproduces it |
| Every source fails (network outage) | Exit code 3 distinguishes this from a quiet news day |
| The GitHub Trending RSS mirror is discontinued | Switch that source to `type: html` with selectors — config change, no code |
| Telegram token revoked or chat id changed | Delivery fails, exit 2, undelivered articles retry on the next successful run |
| Partial multi-message delivery | Seen-marking is per successfully sent message; no duplicates, no losses |
| `history.json` corruption | Atomic writes; a corrupt file is preserved as `.bad` and an empty state is used |
| History eviction inside the freshness window | 800 entries ≈ 20 days versus a 6-hour window; the invariant is documented in §6 |
| Unparseable or missing dates | Dropped by the gate; sources whose model is not "recently published" use `gate: new_only` |
| ffmpeg missing or failing | Video step is skipped and logged; the text digest already arrived |
| Digest too long for one message | Split at category, then item boundaries, never mid-tag |
| Electron release noise | Drafts and prereleases excluded by default |
| Log growth | Rotating handler, 1 MB × 3 |

---

## 12. Source inventory

The 11 source groups from the PRD, expressed in the declarative model. Nine of
the eleven are `type: feed`, which is the evidence behind the §2 deviation.

| Category | Source | Type | Notes |
|---|---|---|---|
| Anthropic | Anthropic News | feed | company announcements |
| Anthropic | Anthropic Engineering | feed | engineering blog |
| Anthropic | Claude Code Changelog | feed | release notes |
| Anthropic | Anthropic Courses | feed | GitHub commits Atom feed for the course repo |
| Anthropic | Anthropic Events | html | `gate: new_only`; selectors TBD, see §4 |
| YouTube | Fireship | feed | channel feed |
| YouTube | AI Explained | feed | channel feed |
| YouTube | Matt Wolfe | feed | channel feed |
| YouTube | Matthew Berman | feed | channel feed |
| YouTube | Two Minute Papers | feed | channel feed |
| Electron | Electron Blog | feed | |
| Releases | Electron | github_release | `electron/electron`; prereleases excluded |
| Releases | Playwright | github_release | `microsoft/playwright` |
| Apple | Apple Developer News | feed | |
| Apple | Swift.org | feed | |
| Trending | GitHub Trending | feed | community RSS mirror; `keywords` and language filtering apply |
| Security | The Hacker News | feed | |
| Security | Bleeping Computer | feed | |
| Security | Krebs on Security | feed | |
| Security | Schneier on Security | feed | |
| Security | SecurityWeek | feed | |
| Security | Google Project Zero | feed | |

**Every feed URL and YouTube channel id in the shipped `config.yaml` must be
verified during implementation** — fetched once, confirmed to return 200 and to
parse into at least one entry with a usable date. The verification run doubles
as fixture capture: the saved response for each distinct source shape becomes a
test fixture. URLs are not asserted in this spec because an unverified URL that
looks plausible is worse than no URL at all.

The GitHub Trending mirror is the least durable source here; §11 records the
fallback to `type: html`.

## 13. Future work

The extension points stay cheap: a new source is usually YAML only, a new
strategy is one module plus a registry entry, a new output is one module in
`dispatchers/`.

- Additional dispatch targets: Slack, Discord, email, desktop notifications.
- Additional collectors: Hacker News front page, ArXiv daily, Product Hunt.
- Per-source `last_run`. Today `last_run` is a single global cutoff, so
  articles dropped by `max_per_source`/`max_total`, or from a source that
  failed to collect on a given run, are never retried: they aren't marked
  seen, but the next run's freshness cutoff has already moved past them.
  A per-source `last_run` would let each source's window hold at the point
  it was last successfully and completely delivered, rather than at the
  global run time, so truncated or failed items get a real chance to be
  delivered on a later run instead of silently aging out. This is a
  deliberate deferral, not an oversight: it interacts with the freshness
  gate, the limits stage, and state persistence closely enough to need its
  own design pass, not a fix-wave patch.
- Narration: the `recap.json` timeline is already the input a TTS step would
  need.
- LLM-generated executive summary of the day's items.
- Publishing the static site to GitHub Pages or an nginx webroot.
- Multi-profile support: separate history files and chat targets.
- Containerized deployment for cloud schedulers.
