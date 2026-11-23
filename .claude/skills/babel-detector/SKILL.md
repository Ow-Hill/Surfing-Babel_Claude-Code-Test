---
name: babel-detector
description: Change Surfing Babel's detector without breaking its statistics. Covers the 18 tests (looks.py: the vision model), the blind-spot probe, empirical calibration (nullmap), re-mining the score log, live refits, the frame-wide sigma and visibility. Use before editing classifier.py, perceptual.py, nullmap.py, calibrate.py, remine.py, visibility.py, looks.py or benchmark.py, or when asked to find more artifacts, add a test, explain a find, or check calibration.
---

# Surfing Babel: detector

## Must
- **Audit against real frames** before and after any calibration change, using
  `scripts/audit_calibration.py`. It uses `calibration.json` next to the log when present.
  Copy *both* files from gh-pages, or the audit checks the wrong calibration. Every test should be about N(0,1) on static. Tests
  clipped at 0 (marked `*`) have sd < 1 by design; judge those by their tail counts.
- **Keep raw statistics identical when optimising.** `tests/test_detector_regression.py`
  pins 20 reference frames to within 2e-3. If you change a statistic on purpose:
  1. regenerate `tests/data/raw_reference.json`;
  2. recalibrate, with `calibrate --simulate-missing` for new tests, then let the workflow's
     `--refit-live` switch them to real frames once 5,000 are logged.
- **Add a new test in all four places:**
  - `TESTS` / `PERCEPTUAL_TESTS`;
  - a simulated map in `calibration.json`;
  - a `describe()` phrase in `analysis.py` and an entry in `TEST_INFO` in `site/app.js`;
  - a signal in `benchmark.py`, prototyped against static before you choose a method.
- **Judge the visibility model by eye.** Before trusting a change, look at the benchmark
  example images and check full-size (640×416) real frames, not the site's 320×208 copies.
  Static and real frames must read about 1 on every cue (`parts`).
- **Group by scan run before calling anything a cluster.** Runs scan consecutive
  locations, so frames from one run share a location prefix.
- **Describe finds honestly.** 4–8σ are the statistical tail of static, and the visibility
  model rates them invisible. Never call them pictures.

## Guards against false positives (keep them)
- **Integrity** (`scan.frame_problem`): only a 640×416 RGB JPEG with Babelia's exact
  quantisation tables is scored, in scans, re-mining and flags. A grey error image scored
  60,000σ, a resized copy 192σ, a re-encoded copy 404σ. All 32 real frames checked pass.
- **Confirmation:** Babelia renders a location byte-identically every time, so every find is
  downloaded again and must match. A third download decides if the copies differ. Five
  flipped bytes made a 347σ "find". Re-mining re-downloads when a score disagrees with the
  log.
- **Rate halt** (`scan.implausible_finds`): a run with far more finds than static gives
  (> expected + 6 sd + 4; about 9 in a 900-frame chunk, where 0.5 are expected) halts.
  - Its finds are quarantined (`stats["quarantine"]`) and taken out of the totals.
  - Scanning stays halted (`stats["halted"]`), and the workflow opens an issue.
  - Resume by running the workflow with `resume=1`.
- **Refit gate** (`calibrate.too_many`): a test whose refit over-counts its own held-out
  frames above 3σ or 4σ (> expected + 5 sd + 3) keeps its previous map.

## Never
- Floor or fudge a test's spread. `max(std, 1.0)` once shrank every σ, leaving 0 finds where
  about 7 were expected.
- Re-mine on every scan. `remine` runs once per `calibration_hash()`
  (`stats["log_calibration"]`), and the marker is written only after its fetches finish.
- Let re-mined or over-cap frames add to `finds_total` (`record(count=False)`).
- Write non-finite scores. Values are clipped to [−8, 60000] (`nullmap.Z_FLOOR` / `Z_MAX`)
  for the float16 log and the JSON.

## Key facts
- **Finds:** any single test at **4σ** or more (the owner's choice). The frame-wide σ (Šidák
  over the tests run) is shown alongside it.
- **Tiers:** static < 3σ, near miss 3–4σ, anomaly 4–8σ, faint 8–16σ, clear 16–40σ, striking
  ≥ 40σ.
- **Speed:** the first 16 tests take about 0.4 s per frame and marks another 0.45 s. Frames
  arrive at about 0.45/s, so classification has several times the headroom it needs. Scans
  classify in a forkserver pool whose initializer is `use_calibration`.

- **Ties: score a value by the frames at or above it.** Colours is stepped (about 700 distinct
  values in 100,000 frames). The upper knots counted only frames strictly above a value, so
  a common step read 4.1 sigma for something 9 in 100,000 frames reach (3.7 sigma). Colours
  then gave 15 frames over 4 sigma where 6 were expected, measured by fitting on one log and
  scoring another (2026-11-17). Continuous tests are unaffected.
- **The weekly audit does the out-of-sample check** (`audit.py`, `audit.yml`). It archives
  non-overlapping log snapshots on the `log-archive` branch (the last 8) and judges the
  calibration only on snapshots scanned after `maps_source.scanned_through`. So pass
  `--scanned-through <stats.scanned>` to `calibrate --from-log` (`refit_live` records it
  itself). An issue opens only for a surplus at least 1.5x expected with p < 0.001. One
  snapshot can't catch a colours-sized fault (2.4x, p 0.005 on 100,000 frames); about three
  pooled snapshots do. To refit, use the archived snapshots as the independent logs.
- **Judge a calibration out of sample, and keep an old log to do it.** gh-pages keeps only
  the latest 100,000 frames, so a copy of an earlier `scores.npy` is the only independent
  sample. Two non-overlapping logs gave the decisive check (fit on one, score the other,
  both ways), and together they gave a 200,000-frame package refit (held-out headline 53
  over 4 sigma against 53.8 expected). One 100,000-frame audit can show a test at 9 against
  3.2 by chance: palette did, then read 10 against 6.3 over both logs.
- **Who refits what.**
  - `refit_live` (after every chunk) only moves a *new* test from simulated to real frames,
    once 5,000 are logged.
  - Refits of tests already on real frames come from the weekly audit. It fits a candidate on
    out-of-sample snapshots except the newest, judges it on the newest, pushes a passing one
    to the `refit-proposal` branch and opens an issue. A person opens the PR and merges it.
    Run it by hand: Actions → "Audit the calibration" → propose = 1.
  - A live `data/calibration.json` whose `base` no longer matches is deleted at scan start.
- **The looks test needs its model** (`python -m babel_surfer.looks download`; workflows use
  `.github/actions/vision-model`, cached and hash-checked). Without it, classify raises; never
  let it score 0. Its raw value is a **log** distance: do not drop the log (the raw distance's
  heavy tail broke nullmap's floor, reading static at up to 28σ). Changing `REVISION`, `K` or
  the preprocessing changes the statistic: re-run `looks fit`, re-fit its map, update
  `tests/data/raw_reference.json`. It is not in `combined_tests`.
- **Watch for single points of failure.** `sensitivity.json` → `load_total.only` counts the
  explored variants that a single test catches alone, today mostly `marks` on drawn strokes and
  outlines. A second test on mean shift was measured and rejected (plan B2). If that count grows,
  or `marks` changes, re-run the probe before merging.
- **Visibility cut-offs (2.0 and 1.4) are unverified against people.** The kit is
  `python -m babel_surfer.rate make` / `fit`. Change `visibility.LIKELY`/`FAINT` only from
  real ratings, never invented ones.
- **Hashes cover only what changes scores** (`classifier.scoring_bytes`: `tests`, `maps`,
  `combined_tests`, `tone_expected`). Metadata such as `maps_source` never triggers
  re-scoring, re-mining or the benchmark; any change to a map does.
- **Probe:** `python -m babel_surfer.sensitivity --explore 40 --seed N --calibration <live
  calibration.json>` takes about 10–15 minutes on 4 cores. Per signal kind, it gives the
  weakest strength found in half the trials and the weakest a person would see (the
  visibility model). Random variants that are seen but missed land in `misses`. Explored
  ids are `V2 + seed*1000 + i`: they draw marks, text and scattered dots, filled with a flat
  colour or a changed static (grey, dark, pale, coarse, blocky, one-channel, gradient). Ids
  below `V2` are the old marks-only generator; never change what an existing id draws,
  because misses and `blind_spots.json` refer to ids.
- **The probe workflow** (`.github/workflows/probe.yml`) runs daily and on detector changes.
  It re-checks earlier misses, moves the ones now caught to `fixed`, and keeps a `history`.
  It lives on the `probe-data` branch, and the site shows it under "Blind spots".
- **Fix a blind spot with a matched statistic,** then pin it in `tests/data/blind_spots.json`
  (`tests/test_sensitivity.py`). The visibility model and the marks test share an idea
  (constant colour along a strip), so judge such fixes by eye too, not only by that model.

## Recipes
- **Calibration audit:**
  `git show origin/gh-pages:data/scores.npy > <scratch>/scores.npy` and the same for
  `data/calibration.json`, then
  `python3 .claude/skills/babel-detector/scripts/audit_calibration.py <scratch>/scores.npy`
- **Refit from real frames:** `python -m babel_surfer.calibrate --from-log <scores.npy>`
  prints a before/after table on held-out frames.
- **Benchmark:** `python -m babel_surfer.benchmark --trials 8 --null 500 --out <dir>`

## Reference
[reference.md](reference.md) covers:
- the pipeline and nullmap details (knots, Pareto tail, atom, outlier trimming);
- re-mining, and the speed tricks that kept results identical;
- matched filters, visibility, and benchmark results.
