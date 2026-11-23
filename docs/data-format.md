# Data files

What the scanner publishes and what each field means. Everything below is on the `gh-pages`
branch under `data/` (served at `<site>/data/…`) unless another branch is named. Times are
Unix seconds (UTC). σ values are calibrated: on pure static each test reads like N(0, 1).

## `status.json`: is it healthy?

| field | meaning |
|---|---|
| `healthy` | a scan finished in the last two hours and scanning is not halted |
| `last_scan_finished_at`, `halted` | when the last scan ended; the halt record, if any |
| `frames_total`, `finds_total`, `best` | all-time totals; the highest score (`location`, `sigma`) |
| `last_24h` | `frames`, `finds`, `scanning_share` (0–1), `fetch_errors` by kind, `rejected_frames` (not genuine Babelia frames), `unconfirmed_finds` (a second download differed), `fingerprint_mean` |
| `calibration` | hash of the calibration's scoring parts |

## `finds.json`, `near_misses.json`, `flags.json`: kept frames

`finds.json` holds the top 100 finds (σ ≥ 4) by score, `near_misses.json` the 12 strongest
frames below 4σ, and `flags.json` the frames visitors flagged. Each entry:

| field | meaning |
|---|---|
| `location` | the frame's location in Babelia's slideshow (a string of up to 16 digits) |
| `sigma`, `tier`, `reason` | the strongest test's score, its tier (anomaly 4–8, faint 8–16, clear 16–40, striking ≥ 40) and which test it was |
| `tests` | every test's σ |
| `combined`, `sigma_global` | all tests' evidence pooled (calibrated); the headline after allowing for 18 tests (Šidák) |
| `visibility` | how much it stands out to a person: `ratio` (× what static shows; ≥ 2 likely visible, 1.4–2 faint), `label`, `cue` and where |
| `description` | one sentence on what the evidence maps show |
| `image`, `maps`, `mask` | a preview JPEG, the evidence maps (JSON) and the tone mask, under `data/images/` |
| `found_at`, `detector`, `assets` | when; the detector version that scored it (saved frames are re-scored when it changes) |

Flags also carry `votes`, `note` and `issue`.

## `finds_log.json`: every find

One entry for every find ever made (`location`, `sigma`, `tier`, `reason`, `found_at`,
`detector`), including those below the top 100 the site shows. Scores are as found, under the
`detector` named, and later re-scoring does not change them. A quarantined run's finds are
removed.

## `history.json`: one line per day

Per UTC `day`:
- `runs`, `frames`, `finds`, `errors` and `error_kinds`;
- `seconds` spent scanning;
- `latency_s_x_frames` and `in_flight_x_seconds` (divide by `frames` and `seconds` for the
  day's latency and requests in flight).

Kept for good.

## `stats.json`: running state

| field | meaning |
|---|---|
| `scanned`, `errors`, `finds_total`, `best` | all-time totals |
| `hist_edges`, `hist` | histogram of every frame's headline σ, all time |
| `hist_current` | the same, since the current detector (`detector`, `since`, `edges`, `counts`) |
| `test_hist` | per-test histograms (`lo`, `step`, `counts[test]`) |
| `fingerprint` | Babelia's 156-row cross-channel fingerprint over all frames (`n`, `sum`, `sumsq`, `min`) |
| `runs` | the last 60 runs. Each has: `started_at`, `finished_at`, `scanned`, `finds`, `errors`, `error_kinds`, `frames_per_hour`, `start_location`, `end_location`, `max_sigma`, `new_finds`, `rejected`, `unconfirmed`, `rescored`, `fingerprint_mean`, `fp_n`, `workers`, `max_workers`, `max_rate`, and `concurrency` (`final`, `max_used`, `history`, `latency_median_s`, `latency_p90_s`, `in_flight`, `by_level`, `pauses`). Re-mining and flag runs carry a `trigger`. |
| `last_run` | the latest scan run |
| `log_calibration` | the calibration the score log was last re-mined for |
| `halted`, `quarantine` | an implausible find rate's halt and its set-aside finds |

## `leaders.json`: top frames per test

For each test and `combined`, the strongest frames by that test, in the same entry format as
the finds.

## `scores.npy`: every frame's scores (the latest 100,000)

A NumPy structured array, one row per frame:

| column | type | meaning |
|---|---|---|
| `location` | uint64 | the frame's location |
| `angled` … `tone` (18 tests, sorted) | float16 | each test's σ under the current calibration (rewritten when the log is re-mined) |
| `combined`, `fingerprint` | float16 | pooled score; the fingerprint's z |
| `raw_angled` … `raw_tone` | float32 | each test's raw statistic, so the log can be re-scored under any calibration (`calibrate.raw_from_log`) |

```python
import numpy as np
log = np.load("scores.npy")
log["raw_colours"][:10]
```

## `calibration.json` (live layer) and `sensitivity.json`

`calibration.json` appears only while a new test is fitted on real frames (`refit_live`). It is
used only when its `base` matches the package calibration's scoring hash.

`sensitivity.json` is copied from the `probe-data` branch: the blind-spot probe's results per
signal family, plus random variants (`misses`, `closest`, `fixed`, `history`). `load` (this run)
and `load_total` (all runs) count, for the explored variants the detector catches, which test
fires first (`first`) and which fires with no other test (`only`).

`audit.json` rows (on `log-archive`) also carry `distinct_per_100k` and `stepped_tail` per test.

## Other branches

- **`log-archive`:** `scores-<scan total>.npz` (a `log` array, as `scores.npy`; frames
  scanned in (total − 100,000, total]) and `audit.json`, the weekly calibration audit. Each
  snapshot is also attached to a monthly release (`data-YYYY-MM`) as open data.
- **`research-babelia`:** the designed uploads behind `docs/research-babelia-generator.md`.
