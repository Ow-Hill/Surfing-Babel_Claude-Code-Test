# Surfing Babel detector: reference

Detail behind [SKILL.md](SKILL.md). Numbers here were checked against the code on 2026-11-07; when in doubt, the code wins.

## Pipeline
1. **`classifier.raw_scores`** computes 16 raw statistics:
   - 10 core tests run per channel on Y, Cb and Cr: shape, blob, lines, texture, palette,
     tone, contrast, angled, repetition, symmetry;
   - 6 perceptual tests (`perceptual.py`) pool the RGB channels as χ²(3): spot, curves, flat,
     colours, spectrum, face.
2. **`calibrate_scores`** maps each raw statistic to σ through `calibration.json["maps"]`
   using `nullmap.sigma`. When a test has no map it falls back to the legacy mean/std.
3. **Headline:** `sigma` is the maximum per-test σ. A find is any test reaching 4σ; the owner
   chose this over a frame-wide threshold.
4. **`sigma_global`** (Šidák over the tests) is shown alongside. A single 4σ test is about
   3.3σ for the whole frame.
5. **`combined`** is a Stouffer sum over `calibration["combined_tests"]`. That stays the
   original ten until the new tests are calibrated on real frames.
6. **Tiers:** static below 3σ, near miss 3–4σ, anomaly 4–8σ, faint 8–16σ, clear 16–40σ,
   striking 40σ and above.

## Calibration (most important lessons)
- **The std floor bug:** never floor a test's standard deviation. The old
  `max(pstdev, 1.0)` shrank every σ (real spreads were 0.3–0.7), so 0 finds came out of 23k
  frames when about 7 were expected.
- **Always audit against real data.** Load `scores.npy` and compare per-test sd and the counts
  above 2σ and 3σ with N(0,1). A test with sd far from 1 is miscalibrated.
- **`nullmap.fit`, the empirical null:**
  - quantile knots for the body;
  - a generalised Pareto tail fitted above p99, with ξ clamped at 0 or above;
  - a slope-1 line far beyond the data, so real pictures still score hundreds of σ;
  - point-mass ("atom") handling for statistics clipped at 0, which take a mid-probability z;
  - outliers excluded before fitting: raw values above 1e5, and exceedances more than 4× the
    p95 exceedance;
  - output clipped to [−8, 60000] (`nullmap.Z_FLOOR`, `Z_MAX`), so the float16 log and the JSON stay finite.
- **Real vs simulated data:**
  - `calibrate --from-log scores.npy` fits on real frames, validates on odd locations and
    prints a before/after table.
  - `calibrate --simulate-missing 6000` covers new tests until the log has 5,000 real frames
    for them.
  - The workflow's `calibrate --refit-live <data>` then writes `<data>/calibration.json`.
- **Rows logged before the raw columns existed** are recovered exactly as
  `raw = z*std + mean`, using the legacy `tests` block.

## Re-mining (`remine.py`)
- **Purpose:** after a recalibration, re-score the whole log from its raw columns, without
  fetching. Then re-fetch only the frames that newly qualify (≤60), using `record(count=False)`.
- **Runs once per calibration:**
  - guarded by `stats["log_calibration"] == classifier.calibration_hash()`;
  - the marker is written only after the fetches finish, so an interrupted run retries.
- **The bug this replaced:** comparing float arrays to detect change failed because the
  combined score was rounded in one path and not the other. Every scan re-fetched 60 frames
  and inflated `finds_total`.
- `count=False` and over-cap finds must never add to `finds_total`.

## Changing a test safely
- **Regression guard:** `tests/test_detector_regression.py` pins the raw statistics of 20
  deterministic frames (`tests/reference_frames.py`, `tests/data/raw_reference.json`, within
  2e-3). Speed-ups must keep it green. A *deliberate* change to a statistic needs a new
  reference **and** a recalibration (`--from-log` once the raw log has the new values, or
  simulated until then).
- **Speed tricks that kept the results identical:**
  - a shared integral image per channel;
  - a cached rotation index plus one `bincount` for angled lines (was PIL rotate ×30);
  - cached autocorrelation constants;
  - one inverse FFT for mirror symmetry (the inverse FFT is linear, so sum first);
  - a float32 FFT for repetition.
- **Speed tricks for the perceptual tests:**
  - a block-mean pyramid (`Frame.level(k)`): large features on half- or quarter-size copies,
    keeping static white;
  - one FFT per colour channel, shared by every test.
- **Result:** all 16 tests run in ~0.42 s per frame, against 0.60 s for the original 10.
  `scan` classifies in a forkserver process pool whose initializer calls `use_calibration`.
- **Matched filters beat generic statistics.** Oriented, elongated Gaussian line filters
  (16×2 px at half size) found thin arcs about 5× more strongly than structure-tensor
  coherence. Prototype alternatives on synthetic signals against ~25 static frames before
  choosing.
- **Adding a test:**
  1. Append it to `TESTS` / `PERCEPTUAL_TESTS`.
  2. Give it a simulated map.
  3. Add `TEST_INFO` in `site/app.js`, a `describe()` phrase in `analysis.py`, and a
     benchmark signal.
  4. Remember that more tests mean more false finds; the site shows the expected rate.

## Visibility (`visibility.py`)
- **Model:** the frame is blurred to viewing scale and converted to CIELAB. The strongest
  patch's ΔE is divided by what pure static reaches at the same size (`STATIC_MAX`, measured
  by `calibrate_constants`).
- **Labels:** ≥2 likely visible, 1.4–2 "barely visible", otherwise invisible.
- **Limitation:** it under-rates thin lines.
- **Finding:** the detector catches almost every benchmark signal while the model rates it
  invisible. All current finds are invisible statistical tails of static. Say so honestly;
  never relabel them as pictures.

## Benchmark and honesty
- `python -m babel_surfer.benchmark --trials 8 --null 500` hides signals in simulated
  static (Babelia's exact JPEG tables) and reports detection, the weakest σ, which test
  caught it, and visibility. Anything blended at 2% is erased by Babel's 16-level
  quantisation (0/8 detected); large pictures and photo-like texture at 5% are caught 8/8.
- Real pictures are about 4096^-266240 likely per frame, so random search never finds one.
  Finds are anomalies, and documentation must keep saying so.
