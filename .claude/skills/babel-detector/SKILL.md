---
name: babel-detector
description: Change Surfing Babel's detector without breaking its statistics. Covers the 16 tests, empirical calibration (nullmap), re-mining the score log, live refits, the frame-wide sigma and visibility. Use before editing classifier.py, perceptual.py, nullmap.py, calibrate.py, remine.py, visibility.py or benchmark.py, or when asked to find more artifacts, add a test, explain a find, or check calibration.
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
- **Describe finds honestly.** 4–8σ are the statistical tail of static, and the visibility
  model rates them invisible. Never call them pictures.

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
- **Speed:** all 16 tests take about 0.4 s per frame. Scans classify in a forkserver pool
  whose initializer is `use_calibration`.

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
