# Runbook

What to do when something goes wrong, as symptom → check → fix. Start every check with
`python .claude/skills/babel-operations/scripts/status.py` (it needs no token), or read
`data/status.json` on the site.

## The site shows old data

- **Check:** status.py, "last Pages deploy" and "live site built from".
- **Fix:**
  - **A Pages deploy failed** (GitHub's side): the scan re-runs a failed deploy once, about
    4 minutes after each publish. If it failed again, re-run "pages build and deployment"
    from the Actions tab.
  - **No scan is running** (no run "in_progress"): run **Scan Babel and publish site** from
    the Actions tab. The hourly cron restarts the relay too.

## Scanning is halted ("implausible find rate")

A scan found far more finds than static can produce, so its finds were moved to
`stats.quarantine` and scanning stopped. An issue was opened.

- **Check:**
  - Look at the quarantined finds: are they all one test, or genuine-looking frames?
  - Check the fingerprint, which should be about 14: is Babelia still serving its usual
    frames?
  - Check the calibration audit (status.py).
- **Fix:**
  - **A broken calibration or test:** fix it first (see the babel-detector skill).
  - **Then resume:** Actions → **Scan Babel and publish site** → resume = 1.

## "Frames lost Babelia's fingerprint"

Every frame Babelia serves carries a 156-row cross-channel correlation (about 14; the lowest of
500,000 was 9.4). A scan averaging below 8 means Babelia changed its generator, or something
alters frames on the way.

- **Check:** fetch a few frames by hand and run `classifier.fingerprint_z` on them. Compare
  with `scan.frame_problem` (format and quantisation tables).
- **Fix:** if Babelia changed, every calibration is invalid. Halt by hand (set
  `stats.halted`), then refit from new frames with the babel-detector skill.

## "The calibration audit found a surplus of high scores"

A test reached 3σ or 4σ at least 1.5 times as often as static should, on frames scanned after
the calibration was fitted, and its finds are likely false.

- **Check:** the issue's table; `audit.json` and `proposal.json` on the `log-archive` branch.
- **Fix:**
  - **A refit was proposed:** open a PR from `refit-proposal`, let CI run, and merge it.
  - **The proposal also fails** on the snapshot it never saw: the fault is in the test's
    statistic. Diagnose with the babel-detector skill. For example, the colours tail is
    stepped and needed its own rule (#44, #49).

## "Something reached 8σ"

That is picture-level for this detector, beyond any static seen so far.

- **Check:** open the find on the site. The find was already re-downloaded and must have
  matched byte for byte. Check its visibility and which test fired.
- **Fix:** if it's a real pattern, celebrate. If it's an artefact (a damaged frame that
  somehow passed, or a broken test), fix the guard or test.

## Babelia is slow or failing

- **Check:** status.py, the "fetch errors by kind" and latency lines.
- **Fix:** usually nothing.
  - **Slow responses or errors:** the scanner already steps down.
  - **Mostly failing:** if a third of requests fail over 5 minutes, it pauses for 3 minutes
    (`Politeness.SPELL_*`).
  - **Down for hours:** the relay stops after a scan that got no frames, and the hourly cron
    retries.
  - **Never** raise the request cap to compensate.

## A test fails in CI

- **Check:** which job.
  - `pytest`: the package tests.
  - `site`: the browser tests (headless Chromium against a real build).
- **Fix:** reproduce locally (`python -m pytest -q`; for the browser tests, `pip install
  playwright` and set `PW_CHROMIUM` to a Chromium binary if Playwright's own isn't
  installed). Never skip or loosen a test to get green.

## Where things live

| What | Where |
|---|---|
| Site and data | `gh-pages` branch (single commit), `data/*.json`, `data/scores.npy` |
| Health summary | `data/status.json`; daily history: `data/history.json`; every find: `data/finds_log.json` |
| Probe results | `probe-data` branch |
| Score-log snapshots, audit | `log-archive` branch |
| Refit proposals | `refit-proposal` branch |
| Agent guidance | `.claude/skills/` (operations, detector, Babelia) |
