# Surfing Babel operations: reference

Detail behind [SKILL.md](SKILL.md). Numbers here were checked against the code on 2026-11-07; when in doubt, the code wins.

## Routine for any change
1. Work on branch `claude/pensive-ritchie-hc4bfy`. When the previous PR is merged, restart
   the branch from `origin/main` first:
   `git fetch origin main && git checkout -B claude/pensive-ritchie-hc4bfy origin/main`.
2. Run `python -m pytest -q` (about 45 s). Run `node --check site/*.js` after
   any JavaScript edit.
3. Commit, push, open a PR to `main` with the GitHub MCP tools, wait for the `Tests` check,
   then merge. The owner has authorised merging to main.
4. Write commit messages and PR bodies in plain language. The owner asked for **no
   Co-Authored-By, session or "Generated with" lines** in commits. Use tool-neutral wording
   ("an AI coding agent"). Never credit another tool.
5. Never put tokens or secrets in the repo or the public site.

## The scan-and-publish workflow (`.github/workflows/scan-and-publish.yml`)
- **Triggers:**
  - hourly cron `:23`, which is unreliable on GitHub and often skipped;
  - `workflow_dispatch` (the Scan now button), with inputs `minutes` (≤300) and `publish_first`;
  - a push to main touching `babel_surfer/`, `site/` or `.github/workflows/`. This scans for
    1 minute, or 0 minutes if only site, docs or tests changed;
  - an issue opened with a `Flag:` title, which runs for 0 minutes.
- **Loop:**
  1. Score the flags.
  2. In 30-minute chunks: `scan`, then `calibrate --refit-live`, `remine`, `build_site`,
     `alerts`, and `publish`.
  3. `publish` makes a single orphan commit force-pushed to `gh-pages`.

  Every step after the scan carries `|| echo ::warning::`, so a chunk is always published.
- **Concurrency group `scan-and-publish`, with cancel-in-progress false:**
  - GitHub keeps only **one pending run**, so each new trigger cancels the older pending one.
    Several "Scan now" clicks while a scan runs therefore cancel each other. Tell the user.
  - A long manual scan (up to 300 min) blocks deploys. Mid-run, if main changed under
    `babel_surfer/`, `site/` or `.github/`, the scan hands its remaining minutes to a new run
    (`-f publish_first=1`). A run that started on older workflow code can't do this.
- **Keep-alive:** the last step calls `PUT .../workflows/scan-and-publish.yml/enable`, which
  resets GitHub's 60-day schedule shutoff.
- **Reliable scheduling:** an external cron service calls the dispatch API with the owner's
  fine-grained token (Actions: read and write); see `docs/external-cron.md`. It was still
  waiting on the owner's token at last check.

## Checking what is live (from the cloud sandbox)
- github.io is **not reachable** from the sandbox: a request returns HTTP 000. Check instead:
  - workflow runs, via
    `curl -s https://api.github.com/repos/Ow-Hill/Surfing-Babel_Claude-Code-Test/actions/workflows/scan-and-publish.yml/runs?per_page=5`
    (public, no token needed);
  - the `pages build and deployment` run in `/actions/runs`;
  - the data itself: `git fetch origin gh-pages` then `git show origin/gh-pages:data/stats.json`.
- **Local preview:**
  1. Copy gh-pages `data/` to the scratchpad (`git archive origin/gh-pages data | tar -x -C …`).
  2. Run `python -m babel_surfer.build_site --out <dir> --offline`. Stamp the benchmark
     fingerprint to skip the slow benchmark rerun.
  3. Serve the folder with `python -m http.server`.
  4. Check it with Playwright: `require('playwright')` works and Chromium is preinstalled.
- **Browser caching:** `build_site.bust_cache` adds `?v=hash` to script and stylesheet links.
  Without it, users kept the old app.js for ~10 minutes ("graph is not updated").

## Data on gh-pages (`data/`)
- **Lists:** `stats.json`, `finds.json` (top 100), `near_misses.json` (12), `leaders.json`
  (top 3 per test plus combined), `flags.json` (People noticed, ≤50).
- **Score log:** `scores.npy` holds the last 100k frames. Each row stores the location (u8),
  per-test z (f2), combined, fingerprint and `raw_<test>` (f4). The log is only ever
  converted to a new format (`Store.upgrade_score_log`), never thrown away.
- **Live calibration:** `calibration.json` is a refit layered over the package calibration.
  It is valid only while its `base` matches the hash of the package calibration.
- **Alerts:** `alerted.json` holds the alert high-water mark.
- **Images:** `images/<loc>.jpg` (a 320×208 preview), `.maps.json` (overlay evidence; the
  server-only keys `eye_parts`/`eye_kind` are stripped) and `.mask.png`.
- **Ids:** each kept entry records `detector` (scores) and `assets` (analysis code). A
  mismatch makes the next scan re-fetch and rebuild that entry.

## Flags ("I see something")
- The site opens a pre-filled issue titled `Flag: <digits>`.
- `babel_surfer/flags.py`:
  1. Groups open issues by location, adding up votes (1 per issue plus 👍).
  2. Applies limits: 3 per author and 20 per run.
  3. Fetches with a 40 s timeout and 1 retry, scores, and stores via `Store.add_flag`.
  4. Replies on the issue and closes it. After 3 failed fetches it closes the issue as
     not_planned.
- Reactions on closed issues are never counted.

## Lessons
- **Merges wait.** A merge can sit "pending" for hours behind a manual scan. Check the run
  list before promising it is live, and schedule a follow-up check (`send_later`) rather than
  polling.
- **Data hiding from git:** `.gitignore` has `data/`, which also hid `tests/data/`. It is
  re-included with `!tests/data/`.
- **Exercising the workflow shell:** extract the run script with `yaml.safe_load`, replace
  `${{ }}` with a placeholder, run `bash -n`, and run it with stubbed `python`, `git` and `gh`
  on PATH.
- **Dependencies:** pinned in `requirements.txt` (numpy, Pillow), because a new Pillow or
  libjpeg can change decoded pixels and so every statistic.
