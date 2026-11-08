# Surfing Babel detector: reference

Detail behind [SKILL.md](SKILL.md). Numbers here were checked against the code on 2026-11-07; when in doubt, the code wins.

## Pipeline
1. **`classifier.raw_scores`** computes 17 raw statistics:
   - 10 core tests run per channel on Y, Cb and Cr: shape, blob, lines, texture, palette,
     tone, contrast, angled, repetition, symmetry;
   - 7 perceptual tests (`perceptual.py`) pool the RGB channels: spot, curves, flat,
     colours, spectrum, face and strokes.
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
  - quantile knots for the body, **extended past p99 up to the value only `TAIL_K`=10 frames
    exceed** (ties handled), so the empirical CDF decides as far as the data reaches;
  - a generalised Pareto tail fitted above p99, with ξ clamped at 0 or above;
  - beyond the last knot, a floor rising at the test's own σ-per-raw pace over its upper
    knots (`slope`, at most 1), so real pictures still score far above every threshold;
  - *Lesson (2026-11-07):* the floor used to rise one-for-one from p99. For tests whose raw
    value outpaces σ (colours: a robust z of counts), that read 1-in-4,000 values as 4.1σ
    instead of about 3.5σ. Dropping the floor entirely went too far the other way: the ξ ≥ 0
    exponential tail understates σ for max-of-Gaussian statistics (2 frames at 4σ where about
    11 were expected). Validate any tail change on held-out frames at both 3σ and 4σ;
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
    It refits again whenever a test's real frames reach `REFIT_GROWTH`=2× the count it was
    last fitted on (`map_sources[t].frames`): a first fit from 5,000 frames has a rough tail
    (curves: 24 frames above 3σ where 12 were expected).
- **Open finding: colours clusters by location.** Its tail frames come in groups, about 3 per
  block (for example 3562822126412…, 781012942002…, 4559865634742…), so neighbouring
  locations share colour-count structure. Held-out frames above 4σ stay too many (4 where
  0.3 are expected) whatever the fit. This bears on the location→image mapping research
  lead in babelia-archive.
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
- **Result:** the first 16 tests run in ~0.42 s per frame, against 0.60 s for the original 10.
  `strokes` adds ~0.4 s. That cost comes from rotating the frame 12 times and taking
  cumulative sums along rows. A neighbour-difference version took 0.05 s but was far weaker
  (σ 0.3–3 against 4–9 on the same marks), so it was rejected.
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

## Blind-spot probe (`sensitivity.py`) and the strokes test
- **First run (2026-11-08, live calibration, 16 tests):**
  - all 23 signal kinds were detected before a person would see them, at the 50% mark;
  - but 9 of 40 random drawn marks were seen first. They were small, dull-coloured outlines,
    ellipses and short thick lines (8–32 px, 1–4 px wide), which no test reached 4σ on.
- **The fix, `strokes`:** the minimum colour variance over 2×16 and 3×32 px strips at 12
  angles, as log(static variance / strip variance). It is calibrated by nullmap: simulated
  first, then switched to real frames by `refit_live` after 5,000 logged frames.
- **Calibration hand-over:** the package calibration was set to the live (real-frame) one plus
  the strokes map. A new package calibration invalidates the live layer (`base` hash), and
  this avoids a window where the six perceptual tests fall back to their simulated maps.

## Visibility (`visibility.py`)
- **Model:** three cues in CIELAB, each divided by what pure static reaches at the same size
  (mean of the strongest patch over 24 simulated frames; `calibrate_constants()` re-measures
  them all), so static reads about 1 on each:
  - **colour:** the strongest blurred patch's ΔE from the frame's median (`STATIC_MAX`);
  - **texture:** a patch (8–64 px, or the whole frame) smoother or busier than static,
    as |log(detail / static's)|, never judged against less than a 10% change (`WEBER`);
  - **line:** a 2–4 px × 24–64 px strip at 12 angles whose colour variance is far below
    static's (`LINE_MAX`). The point and `angle` (strip along (cos, sin) in image
    coordinates, y down) are drawn by the eye layer of the overlay.
- **Labels:** ≥2 likely visible, 1.4–2 "barely visible", otherwise invisible. Output keeps
  `ratio`/`label`, and adds `cue`, `parts`, `x`, `y`, `patch` (and `smoother`, `angle`).
- **Lesson (2026-11-07):** the old colour-only model rated a full-strength 2 px line, a flat
  grey 64 px square, greyscale static and dark static all *invisible* (0.6–1.1). A thin
  line or a smooth patch hardly moves a patch's mean colour; the eye sees them because
  static varies from pixel to pixel and they don't. **Judge the model by eye:** look at the
  benchmark example images (gh-pages `benchmark/example_*.jpg`) before trusting its labels.
- **Check on real frames, at full size.** The site's `data/images/*.jpg` are 320×208
  re-encodes and read about 0.5 on the old scale; they are not a test set. Fetch a few
  full 640×416 frames serially (finds and random locations): with the new model they read
  like simulated static (median 1.1, max 1.43 over 32, both from the unchanged colour cue).
- **Cost:** about 1.8 s per frame; it runs only on saved frames and the benchmark.
- **Finding:** finds are still invisible statistical tails of static. Many benchmark signals
  are caught before they are visible, but lines and smooth patches at the strengths the
  detector needs are visible too. Say so honestly; never relabel finds as pictures.

## Benchmark and honesty
- `python -m babel_surfer.benchmark --trials 8 --null 500` hides signals in simulated
  static (Babelia's exact JPEG tables) and reports detection, the weakest σ, which test
  caught it, and visibility. Anything blended at 2% is erased by Babel's 16-level
  quantisation (0/8 detected); large pictures and photo-like texture at 5% are caught 8/8.
- Real pictures are about 4096^-266240 likely per frame, so random search never finds one.
  Finds are anomalies, and documentation must keep saying so.
