# Improvement plan (November 2026)

A roadmap of what could make Surfing Babel better, grounded in what the scan, the probe and the
audits have measured so far. Each item says what it is, why (with the evidence), its effort
(**S** an hour or two, **M** a day, **L** several days, **R** open-ended research), and how to
tell it worked. The ten items worth doing next are listed first, then everything by theme.

## Where things stand (2026-11-17)

| | |
|---|---|
| Frames scanned | 528,000, about 2,200 an hour (53,700 a day), scanning 99% of the time |
| Finds (≥ 4σ) | 235, about 1 in 1,950 frames, as pure static predicts (1 in 1,860) |
| Best find | 5.52σ; the expected best of 9 million N(0, 1) draws (528,000 frames × 17 tests) is about 5.2σ |
| Visible to a person | 98 of the 100 finds on the site are "invisible" by the visibility model; 2 are faint |
| Calibration | refitted on 200,000 frames; held out, 53 frames over 4σ against 53.8 expected |
| Blind spots | none: 23 signal families and 13 probe runs of random variants are all caught before a person sees them |
| Throughput limit | Babelia itself: about 8 requests at once × 12–14 s each; the scanner sits at the agreed cap (8, 40/min) |
| Visitor flags | 0 ever |
| Tests | 177, 89% line coverage, 3 minutes |

What follows from these numbers:

1. **The detector is honest and sensitive, and the finds are chance.** They arrive at static's
   rate, the best one is what chance produces at this scale, and almost none are visible. The
   search is a well-measured lottery: a picture in random static is astronomically unlikely
   (about 4096^−266,240 per frame), so faster or more sensitive scanning cannot change what it
   finds.
2. **So the biggest wins are not "more of the same".** They are:
   - say plainly what a find is;
   - look for what people find interesting (frames that *look like* something), not only for
     what is statistically rare;
   - keep the statistics trustworthy automatically;
   - the one lead that could change the search entirely: Babelia's generator.

## The ten to do next

| # | Item | Effort | Why now |
|---|---|---|---|
| 1 | **A1** Show the expected best score so far | S | The headline number, 5.52σ, reads as remarkable, and chance explains it |
| 2 | **C1** Hash only what changes scores | S | A metadata edit re-fetched every saved find this week |
| 3 | **C2** Retire the dead live-refit path; refits come from the audit | M | `refit_live` can no longer fire (100,000-frame log, doubling rule) |
| 4 | **A2** Lead with "looks like something", not σ | M | 98 of 100 finds are invisible; σ is what the site leads with |
| 5 | **B1** A calibrated "looks like" test (pareidolia) | L | The agreed next detector phase; the one change that alters *which* frames surface |
| 6 | **E1** Probe the generator with designed uploads | M | The only route to local generation, which would remove Babelia's throughput limit |
| 7 | **H1** Browser smoke tests in CI | M | The site's JavaScript has no tests beyond the search demo's maths |
| 8 | **F1** Long-term run history | S | `stats.json` keeps 60 runs (about 30 hours); trends beyond that are lost |
| 9 | **J1** Pin GitHub Actions to commit SHAs | S | The workflows hold write tokens; tags can move |
| 10 | **G1** Publish the score-log archive as open data | M | 528,000 frames of measured static with 17 statistics each; nothing comparable exists |

---

## A. Say what a find is

| # | Item | Effort | Evidence and plan | Measure |
|---|---|---|---|---|
| A1 | **Expected best so far** | S | The best find is 5.52σ. The best of the N × 17 test scores scanned so far is expected near 5.2σ (median 5.25). Show "best so far 5.52σ, chance alone gives about 5.2σ at this many frames" next to the stat, and a dashed "chance" line on the histogram. | A visitor can tell from the page that the record is not a discovery |
| A2 | **Lead with what a person would see** | M | Sort and badge finds by visibility, not σ. Give "faint" and "likely visible" finds their own row. Collapse the invisible majority behind "statistically rare, but invisible (98)". | Time on detail views of visible finds; fewer "I don't see anything" reactions |
| A3 | **Whole-scan significance per find** | S | Each find already shows a frame-wide σ (all 17 tests). Add the scan-wide view in words: one test at 4σ is about a 1-in-1,860-frame event for static, so about 29 such frames are expected a day at the current pace. | Fewer finds read as evidence of something |
| A4 | **A short honest summary at the top** | S | One sentence under the title: "Random static almost never contains a picture; this site measures how strange it gets." The rest of the page then reads correctly. | Usability-review heuristic 2 (match with the real world) |
| A5 | **Flags: promote or retire** | S | No visitor has ever flagged a frame. Either show the button on every frame and in the live strip, or remove the feature and its workflow step (it runs at the start of every scan run). | Flags per week; or less code |

## B. Detection: find what people find interesting

| # | Item | Effort | Evidence and plan | Measure |
|---|---|---|---|---|
| B1 | **A calibrated "looks like" test** | L | A small pretrained vision model (CLIP-style, CPU) scores each frame against prompts ("a face", "a letter", "a landscape", "a figure"). The score goes through a null map fitted on real static, like every other test, so it reads in honest σ. Run it on the top 1–2% of frames only, to keep CPU in budget (scoring takes about 0.9 s a frame on one core; the 4-core runner has several times the capacity the scan needs). | Held-out calibration as for every test; a benchmark row per prompt; a "looks like" leaderboard |
| B2 | **Redundancy for drawn marks** | M | All five of the probe's closest calls are caught only by `marks`. One weak point would open a blind spot across the whole family. Extend `curves` or `lines` to catch thick short strokes as a second line of defence. | Probe "caught by" lists two tests for those variants |
| B3 | **Probe textures against every test** | M | The wider explorer (text, dots, textured fills) finds no misses, but it only records the five closest calls. Keep per-test "first detector" counts across runs to see which tests carry the load. | A table of tests by variants caught first |
| B4 | **Check the visibility model with people** | M | The model decides what counts as "visible" (and so what a blind spot is), and it has never been checked against human judgement. Run a small labelled set (50 frames, a few raters) and fit the 1.4 and 2.0 cut-offs to it. | Agreement between model and raters |
| B5 | **Re-examine stepped statistics** | S | Colours was stepped and mis-scored (#44); flat is partly stepped. List every test's distinct-value count in the audit and keep the tie rule covered by tests. | Audit column "distinct values per 100,000" |

## C. Keep the statistics trustworthy

| # | Item | Effort | Evidence and plan | Measure |
|---|---|---|---|---|
| C1 | **Hash only what changes scores** | S | `detector_id` and `calibration_hash` hash the whole calibration file. Recording `scanned_through` (metadata only) made the scan re-fetch every saved find. Hash `tests`, `maps`, `combined_tests` and `tone_expected` only. | A metadata edit triggers no re-scoring |
| C2 | **Refits from the audit, not `refit_live`** | M | `refit_live` waits for twice the frames a test was fitted on, and the log holds 100,000, so it never fires again. Replace it: when the audit has two or more out-of-sample snapshots, a workflow fits on all but one, checks on the last, and opens a PR with the report. A person merges it, never an automatic push. | Refit PRs with held-out checks; no stale `refit_live` code |
| C3 | **Remove the stale live calibration** | S | `data/calibration.json` on gh-pages was fitted on an older package calibration. The scan ignores it, but it misled the audit script once. Delete it when its `base` doesn't match. | No ignored calibration on the site |
| C4 | **Audit the headline and the combined score too** | S | The weekly audit judges each test and the headline (max of tests). Add the combined score and its leaderboard. | The combined score appears in `audit.json` |
| C5 | **Watch Babelia's fingerprint** | S | The 156-row cross-channel fingerprint is monitored but not alerted on. If Babelia changed its generator, every calibration would be wrong at once. Alert when the fingerprint's z drops below 8 for a whole chunk. | An issue on a fingerprint change |

## D. Throughput and politeness

The scan is limited by Babelia, not by the code: about 2,200 frames an hour at the agreed cap
(8 at once, 40 a minute). More frames would not change what is found (see the numbers above),
so most items here are about running smoothly, not faster.

| # | Item | Effort | Evidence and plan | Measure |
|---|---|---|---|---|
| D1 | **Start each chunk where the last ended** | S | Every 30-minute chunk starts at 3 requests and takes 1–3 minutes to reach 7–8 (about 2–4% of its frames). Start at the previous chunk's final level, capped at the agreed maximum. | Frames an hour, +2–4% |
| D2 | **Pool per-level latency across runs** | S | Per-run `by_level` medians are skewed (low levels are only sampled at the start and end of a run). Pool them across runs in the status script, the only fair basis for judging the cap. | A pooled latency-by-level table |
| D3 | **Handle Babelia's bad spells** | M | 48 HTTP 500s came in one 3.5-hour spell, when frames an hour fell to about 1,000. On a spell (say, a third of requests failing over 5 minutes), pause for a few minutes rather than keep stepping down and back up. | Fewer errors per spell; no extra load during one |
| D4 | **Local generation** | R | See E. Only this removes Babelia's limit (CPU-bound: about 15,000 frames an hour on one 4-core runner, with no load on Babelia). | — |

## E. The generator: the one lead that changes the search

Known so far (`docs/research-babelia-generator.md`):
- A location is one base-16 digit per frame value (798,724 digits for 798,720 values).
- Neighbouring locations differ by a constant in one block of the frame.
- The frame is four 160 px strips read like text, in 32 blocks of 52 rows.
- The mixing is bitwise with a fixed pattern, yet no digit of the location appears directly
  in the frame.

| # | Item | Effort | Plan | Measure |
|---|---|---|---|---|
| E1 | **Designed uploads to the image search** | M | Work backwards from images. Upload pairs that differ in one pixel (or one value) to `/imagesearch.cgi` and compare the returned locations: which digits change, and how, is the inverse map, one position at a time. Use solid colours and PNG, so there is no JPEG recovery noise. A few dozen uploads, spaced out, are polite. | The location digits that depend on a chosen pixel |
| E2 | **Exactly known frames** | M | Every analysis so far is limited by recovering values from JPEG (82% exact). A frame from an uploaded, losslessly known image is exact, so the algebra in the research notes (A from two pairs) becomes possible. | A·L + B tested on exact pairs |
| E3 | **Model one block's mixing** | R | Start from the 16-row band just before the exact block (low two bits exact, bits 2–3 disturbed at a fixed quarter of positions) and find what the disturbed bits are combined with. | Predict the band at the JPEG ceiling |
| E4 | **If solved: generate locally** | L | A generator in numpy, verified frame for frame against Babelia, scanning at CPU speed. Babelia is then needed only to confirm finds. | Verified frames; frames an hour × 10 |

## F. Reliability and operations

| # | Item | Effort | Plan | Measure |
|---|---|---|---|---|
| F1 | **Long-term run history** | S | `stats.json` keeps the last 60 runs (about 30 hours). Append a daily summary (frames, finds, errors by kind, latency, in-flight) to `data/history.json` for trends over months. | A daily history file; a trend chart on the site |
| F2 | **A machine-readable status** | S | Publish `data/status.json` (last scan, scanning share, halted, audit status, probe status), so a person or another tool can check health without the GitHub API. | One file answers "is it healthy" |
| F3 | **Retry a failed Pages deploy** | S | One deploy failed on GitHub's side (02:48 UTC, 10 November) and the site stayed stale until the next chunk. The publish step could re-trigger once on failure. | No 30-minute stale windows |
| F4 | **A one-page runbook** | S | Halt, resume, refit, audit issue, Babelia down, Pages failing: each as symptom, check, fix. The skills hold this for an agent; people need it too. | `docs/runbook.md` |
| F5 | **Actions minutes accounting** | S | Show in the status script how many runner minutes the scan, probe and audit use a month, before it matters. | A monthly number |

## G. Data and openness

| # | Item | Effort | Plan | Measure |
|---|---|---|---|---|
| G1 | **Publish the score logs** | M | The `log-archive` branch keeps 8 snapshots. Attach each snapshot to a monthly GitHub release as an open dataset, with a data card: columns, calibration, how frames were chosen. | Downloadable monthly datasets |
| G2 | **Keep every find** | S | `finds.json` shows the latest 100; there have been 235. Keep the rest in an archive file the site can page through. | All finds reachable |
| G3 | **Document the data files** | S | `data/*.json` and `scores.npy` are read by the site and could be read by others; their fields are documented nowhere. | `docs/data-format.md` |

## H. The website

| # | Item | Effort | Plan | Measure |
|---|---|---|---|---|
| H1 | **Browser smoke tests in CI** | M | Playwright (headless Chromium): load the built site against fixture data, open a find, step through the search demo, switch dark mode, and fail on console errors or horizontal overflow at 390 px. This is the class of bug found by hand this month (the nowrap verdict). | CI fails on a broken page |
| H2 | **An accessibility check** | S | Run axe-core in the same browser test; fix what it reports (contrast, labels, focus order). | No serious axe violations |
| H3 | **A performance budget** | S | The site loads 140 KB of code plus `stats.json` (86 KB) and up to 100 thumbnails. Lazy-load below-the-fold images and set a budget in the browser test. | Largest Contentful Paint under 2.5 s on a mid phone |
| H4 | **The search demo, one step further** | S | Let the demo's "scan 1,000 frames" also show the expected-best curve from A1, so the lesson ("the record grows with the number of frames") is seen, not read. | — |

## I. Engineering and tests

| # | Item | Effort | Plan | Measure |
|---|---|---|---|---|
| I1 | **Lint in CI** | S | `pyflakes` is clean today; add it (or ruff) to the Tests workflow so it stays clean. | Lint step in CI |
| I2 | **Parallel tests** | S | The suite takes 3 minutes. pytest-xdist on the 4-core runner could cut it to about 1. The timing-sensitive tests are now structural (#42), so they tolerate it. | CI time |
| I3 | **Dependency updates on a schedule** | S | numpy and Pillow are pinned because decoding changes the statistics. A monthly job could try the latest versions against `test_detector_regression.py` and open a PR only if the raw statistics are unchanged. | Up-to-date pins without silent drift |
| I4 | **Type hints on the public functions** | M | The package is typed in places. Finish it for `scan`, `storage` and `calibrate` and check with mypy in CI. | mypy clean |
| I5 | **Recorded fixtures for network paths** | S | The demo download and probe-results fetch are the main untested lines. Recorded responses make them testable offline. | Coverage of `seeded.py` above 85% |

## J. Security

| # | Item | Effort | Plan | Measure |
|---|---|---|---|---|
| J1 | **Pin Actions to commit SHAs** | S | The workflows use `actions/checkout@v4` and `setup-python@v5` with write permissions to contents and issues. Pin to full SHAs, with a comment naming the version. | No tag-pinned actions |
| J2 | **Least-privilege permissions** | S | Review each workflow's `permissions`. For example, the probe needs contents write only for its branch, so a separate job could hold that token. | Permissions per job, not per workflow |
| J3 | **The "Scan now" token** | S | The owner's fine-grained token stays in `localStorage` (sent only to api.github.com). Offer "this session only" (`sessionStorage`) as the default, and remind the owner of the token's expiry and scope (Actions on this repository only). | No long-lived token by default |

---

## How to use this plan

- Work in rounds, one theme or a few small items at a time, each through a PR with its own
  measurement (as every round so far).
- Mark items done here with the PR number.
- **Before starting B1 or E1, check them with the owner.**
  - **B1** adds a model dependency and CPU cost.
  - **E1** makes uploads to Babelia's image search, which is polite at a few dozen spaced over
    days, but is new traffic of a different kind.
- Items marked **R** are research: time-box them and record what is ruled out, as
  `research-babelia-generator.md` does.
