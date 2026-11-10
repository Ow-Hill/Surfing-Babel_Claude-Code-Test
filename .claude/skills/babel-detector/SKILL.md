---
name: babel-detector
description: Change Surfing Babel's detector without breaking its statistics. Covers the 17 tests, the blind-spot probe, empirical calibration (nullmap), re-mining the score log, live refits, the frame-wide sigma and visibility. Use before editing classifier.py, perceptual.py, nullmap.py, calibrate.py, remine.py, visibility.py or benchmark.py, or when asked to find more artifacts, add a test, explain a find, or check calibration.
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

- **Live refits stop once a test is fitted on 50,000 or more frames.** `refit_live` waits for
  `REFIT_GROWTH`× the frames a test was fitted on, but `scores.npy` keeps only the last
  100,000. After the package refit from 100,000 frames, only a package calibration change
  starts a new live fit. A live `data/calibration.json` whose `base` no longer matches is
  ignored by the scan (`activate_live_calibration` returns None), and
  `scripts/audit_calibration.py` now ignores it the same way. On 2026-11-10 it had audited
  that stale file and reported flat at sd 2.0 when the scan's calibration gives 0.88.

## Find what the detector misses (the learning loop)
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
