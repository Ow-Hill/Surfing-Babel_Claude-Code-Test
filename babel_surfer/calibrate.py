"""Measure the detector's null distribution and write ``calibration.json``.

Babelia frames are 640x416, every pixel an independent uniformly random
12-bit colour (16 levels per channel), JPEG-encoded (quality ~90, no chroma
subsampling). We simulate exactly that with Babelia's own quantization tables, run every test, and record the
mean / std of each raw statistic. Optionally, real frames downloaded from
Babelia are scored with the result to confirm simulation and reality agree.

    python -m babel_surfer.calibrate --frames 400 --real-dir path/to/jpgs

With a score log from real scanning (``data/scores.npy``), learn each test's
null distribution from real frames instead (see ``nullmap.py``). This is the
calibration the live detector uses:

    python -m babel_surfer.calibrate --from-log public/data/scores.npy
"""

from __future__ import annotations

import argparse
import io
import functools
import json
import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from . import classifier, nullmap
from .classifier import CALIBRATION_FILE, TESTS, raw_scores, tone_histogram

W, H = 640, 416
LEVELS = np.arange(16) * 17          # 0, 17, ..., 255

# Babelia's exact JPEG quantization tables (read from real frames). Using
# them, rather than Pillow's "quality=90", makes simulated static match real
# static even on the very sensitive tone test.
BABELIA_QTABLES = [
    [3, 2, 2, 3, 4, 6, 8, 10, 2, 2, 2, 3, 4, 9, 10, 9, 2, 2, 3, 4, 6, 9, 11, 9,
     2, 3, 4, 5, 8, 14, 13, 10, 3, 4, 6, 9, 11, 17, 16, 12, 4, 6, 9, 10, 13, 17,
     18, 15, 8, 10, 12, 14, 16, 19, 19, 16, 12, 15, 15, 16, 18, 16, 16, 16],
    [3, 3, 4, 8, 16, 16, 16, 16, 3, 3, 4, 11, 16, 16, 16, 16, 4, 4, 9, 16, 16,
     16, 16, 16, 8, 11, 16, 16, 16, 16, 16, 16] + [16] * 32,
]


def encode_like_babelia(rgb: np.ndarray) -> bytes:
    """JPEG-encode an RGB uint8 array the way Babelia's server does."""
    buf = io.BytesIO()
    Image.fromarray(rgb.astype(np.uint8)).save(buf, "JPEG", qtables=BABELIA_QTABLES,
                                               subsampling=0)
    return buf.getvalue()


def babel_static(rng: np.random.Generator) -> bytes:
    """One simulated Babelia frame, as the JPEG bytes the server would send."""
    return encode_like_babelia(LEVELS[rng.integers(0, 16, (H, W, 3))])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20261104)
    ap.add_argument("--real-dir", type=Path)
    ap.add_argument("--out", type=Path, default=CALIBRATION_FILE)
    ap.add_argument("--from-log", type=Path,
                    help="fit empirical null maps from a real-frame score log (scores.npy)")
    ap.add_argument("--simulate-missing", type=int, metavar="FRAMES",
                    help="fit null maps on this many simulated frames for tests without one")
    ap.add_argument("--refit-live", type=Path, metavar="DATA_DIR",
                    help="(workflow) refit tests whose real-frame log is now large enough")
    args = ap.parse_args(argv)
    if args.refit_live:
        refit_live(args.refit_live)
        return 0
    if args.simulate_missing:
        for t, v in calibrate_simulated(args.out, args.simulate_missing).items():
            print(f"{t:10s} held-out sd {v['sd']:.2f}  >3σ {v['over_3']}/{v['expected_3']}  max {v['max']}")
        return 0
    if args.from_log:
        report = calibrate_from_log(args.from_log, args.out)
        print(format_report(report))
        return 0

    rng = np.random.default_rng(args.seed)

    # Pass 1: the null colour histogram (for the tone test).
    hist = np.zeros((3, 16))
    for _ in range(max(50, args.frames // 4)):
        rgb = np.asarray(Image.open(io.BytesIO(babel_static(rng))).convert("RGB"))
        hist += tone_histogram(rgb)
    tone_expected = hist / hist.sum(axis=1, keepdims=True)

    # Pass 2: null distribution of every raw test statistic (in parallel).
    frames = [babel_static(rng) for _ in range(args.frames)]
    samples = {t: [] for t in TESTS}
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 1) as ex:
        for i, raw in enumerate(ex.map(functools.partial(raw_scores, tone_expected=tone_expected),
                                       frames, chunksize=8)):
            for t, z in raw.items():
                samples[t].append(z)
            if (i + 1) % 100 == 0:
                print(f"  simulated {i + 1}/{args.frames}", file=sys.stderr)

    # The true spread: an earlier version floored it at 1.0, which shrank
    # every sigma (the raw statistics are maxima with spreads of 0.3-0.7).
    tests = {t: {"mean": statistics.fmean(v), "std": max(statistics.pstdev(v), 1e-6),
                 "max": max(v)} for t, v in samples.items()}
    tone_source = "simulated"

    # Babelia's own JPEG encoder rounds very slightly differently from
    # Pillow's (~0.5% curvature across the histogram), which the tone test
    # is sensitive enough to notice. When real frames are available, learn
    # the tone baseline from them instead, with leave-one-out validation.
    real_rgb = []
    if args.real_dir:
        for f in sorted(args.real_dir.glob("*.jpg")):
            try:
                real_rgb.append(np.asarray(Image.open(f).convert("RGB")))
            except Exception as e:
                print(f"  skip {f.name}: {e}", file=sys.stderr)
    if len(real_rgb) >= 10:
        hists = np.stack([tone_histogram(a) for a in real_rgb]).astype(float)
        total = hists.sum(axis=0)
        loo = []
        for i, h in enumerate(hists):
            rest = total - h
            loo.append(classifier._test_tone(real_rgb[i],
                                             rest / rest.sum(axis=1, keepdims=True)))
        tone_expected = total / total.sum(axis=1, keepdims=True)
        tests["tone"] = {"mean": statistics.fmean(loo),
                         "std": max(statistics.pstdev(loo), 1.0), "max": max(loo)}
        tone_source = f"real Babelia frames (n={len(real_rgb)}, leave-one-out)"

    # Combined score: the per-test calibrated z summed (Stouffer). Tests are
    # correlated, so its own null mean/std are measured on the same frames.
    comb = []
    for i in range(len(samples[TESTS[0]])):
        zs = {t: (samples[t][i] - tests[t]["mean"]) / tests[t]["std"] for t in TESTS}
        comb.append(classifier.combined_raw(zs))
    tests["combined"] = {"mean": statistics.fmean(comb), "std": max(statistics.pstdev(comb), 1e-6),
                         "max": max(comb)}

    result = {"frames": args.frames, "model": "uniform 16-level RGB, JPEG with Babelia qtables, 4:4:4",
              "tone_source": tone_source,
              "tests": tests, "tone_expected": tone_expected.round(6).tolist()}

    # merge: keep the empirical maps (and their sources) already in the file
    if args.out.exists():
        merged = json.loads(args.out.read_text())
        merged.update(result)
        result = merged
    args.out.write_text(_compact_json(result))
    classifier.use_calibration(args.out)
    print(f"wrote {args.out}")

    # Null distribution of the final headline sigma on fresh simulated frames.
    rng2 = np.random.default_rng(args.seed + 1)
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 1, initializer=classifier.use_calibration,
                             initargs=(str(args.out),)) as ex:
        sim = list(ex.map(classifier.classify,
                          [babel_static(rng2) for _ in range(max(100, args.frames // 2))],
                          chunksize=8))
    sim_sigmas = [c.sigma for c in sim]
    print(f"simulated static: max sigma {max(sim_sigmas):.2f}, "
          f"mean {statistics.fmean(sim_sigmas):.2f} (n={len(sim_sigmas)})")
    result["null_sigma"] = {"simulated_max": max(sim_sigmas),
                            "simulated_mean": statistics.fmean(sim_sigmas),
                            "simulated_n": len(sim_sigmas)}

    if args.real_dir:
        files = sorted(args.real_dir.glob("*.jpg"))
        real = []
        for f in files:
            try:
                real.append(classifier.classify(f.read_bytes()))
            except Exception as e:      # truncated download etc.
                print(f"  skip {f.name}: {e}", file=sys.stderr)
        if real:
            sig = [c.sigma for c in real]
            worst = max(real, key=lambda c: c.sigma)
            print(f"real Babelia frames: max sigma {max(sig):.2f}, "
                  f"mean {statistics.fmean(sig):.2f} (n={len(sig)}); "
                  f"worst test {worst.reason}")
            result["null_sigma"].update(real_max=max(sig),
                                        real_mean=statistics.fmean(sig),
                                        real_n=len(sig))
    args.out.write_text(_compact_json(result))
    return 0


MIN_LOG_FRAMES = 5000
REFIT_GROWTH = 2.0          # refit a test once the log has this many times the frames it was fitted on


def raw_from_log(log, legacy: dict) -> dict:
    """Each test's raw statistic for every logged frame. Logs written before
    raw columns existed hold ``(raw - mean) / std`` under the calibration in
    ``legacy`` (calibration.json's ``tests``), which is exactly invertible."""
    from .storage import Store
    log = Store.upgrade_score_log(log)
    out = {}
    for t in TESTS:
        raw = log[f"raw_{t}"].astype(float)
        c = legacy.get(t, {"mean": 0.0, "std": 1.0})
        missing = ~np.isfinite(raw)
        raw[missing] = log[t][missing].astype(float) * c["std"] + c["mean"]
        out[t] = raw
    return out


def apply_maps(maps: dict, raw: dict, combined_tests=None) -> tuple[dict, np.ndarray]:
    """Per-test sigma arrays (NaN for tests without a map or a value) and the
    calibrated combined score, pooled over ``combined_tests``."""
    n = len(next(iter(raw.values())))
    # rounded and clipped exactly like classify(), so the log stays finite in
    # float16 and re-scoring gives the same numbers a live scan would
    z = {t: (np.round(np.clip(nullmap.sigma_array(maps[t], raw[t]), -9.0, 6.0e4), 3) if t in maps
             else np.full(n, np.nan)) for t in TESTS}
    names = [t for t in (combined_tests or classifier.CORE_TESTS) if t in maps]
    stack = np.stack([z[t] for t in names]) if names else np.zeros((1, n))
    k = np.isfinite(stack).sum(axis=0)
    comb_raw = np.where(k > 0, np.nansum(stack, axis=0) / np.sqrt(np.maximum(k, 1)), np.nan)
    comb = (nullmap.sigma_array(maps["combined"], comb_raw) if "combined" in maps
            else comb_raw)
    return z, np.round(np.clip(comb, -9.0, 6.0e4), 3)


def _counts(z: np.ndarray, of: int = 1) -> dict:
    """Exceedance counts vs what N(0,1) predicts (for the max of ``of``
    independent tests when of > 1)."""
    z = z[np.isfinite(z)]
    tail = lambda k: float(len(z) * (1 - statistics.NormalDist().cdf(k) ** of))
    return {"n": int(len(z)), "sd": round(float(z.std()), 3), "max": round(float(z.max()), 2),
            **{f"over_{k}": int((z > k).sum()) for k in (2, 3, 4)},
            **{f"expected_{k}": round(tail(k), 1) for k in (2, 3, 4)}}


def ready_tests(raw: dict) -> list[str]:
    """Tests with enough real frames in the log to be calibrated on them."""
    return [t for t in TESTS if int(np.isfinite(raw[t]).sum()) >= MIN_LOG_FRAMES]


def calibrate_from_log(log_path: Path, out: Path = CALIBRATION_FILE,
                       base: Path | None = None) -> dict:
    """Fit empirical null maps for every test with at least MIN_LOG_FRAMES
    real frames in the log; other tests keep their current (simulated) maps.
    ``base``: the calibration to start from (default: ``out`` itself)."""
    src = base or out
    data = json.loads(src.read_text()) if src.exists() else {}
    legacy = data.get("tests") or {}
    log = np.load(log_path)
    raw = raw_from_log(log, legacy)
    ready = ready_tests(raw)
    if not ready:
        raise SystemExit(f"no test has {MIN_LOG_FRAMES} logged frames yet ({len(log)} rows)")
    odd = (log["location"] % 2).astype(bool)
    keep = {t: m for t, m in (data.get("maps") or {}).items() if t not in ready and t != "combined"}

    def fit_maps(rows):
        maps = dict(keep)
        maps.update({t: nullmap.fit(raw[t][rows]) for t in ready})
        z, comb_raw = apply_maps(maps, {t: raw[t][rows] for t in TESTS}, ready)
        maps["combined"] = nullmap.fit(comb_raw)
        return maps

    # validation: fit on even locations, test on odd ones
    half = fit_maps(~odd)
    zv, cv = apply_maps(half, {t: raw[t][odd] for t in TESTS}, ready)
    validation = {t: _counts(zv[t]) for t in ready}
    validation["combined"] = _counts(cv)
    peak = np.nanmax(np.stack([zv[t] for t in ready]), axis=0)
    validation[f"headline (max of {len(ready)})"] = _counts(peak, of=len(ready))
    # before: the calibration in use until now, on the same frames
    old_maps = data.get("maps") or {}
    before = {}
    for t in ready:
        if t in old_maps:
            before[t] = _counts(nullmap.sigma_array(old_maps[t], raw[t][odd]))
        elif t in legacy:
            before[t] = _counts((raw[t][odd] - legacy[t]["mean"]) / legacy[t]["std"])

    data["maps"] = fit_maps(np.ones(len(log), bool))          # final: all frames
    data["combined_tests"] = ready
    sources = data.get("map_sources") or {}
    for t in ready:
        sources[t] = {"source": "real", "frames": int(np.isfinite(raw[t]).sum())}
    data["map_sources"] = sources
    data["maps_source"] = {"frames": int(len(log)), "log": log_path.name,
                           "method": "empirical CDF + generalised Pareto tail (xi >= 0)",
                           "validation": validation}
    out.write_text(_compact_json(data))
    classifier._calibration_cache = None
    return {"validation": validation, "before": before, "ready": ready}


def _simulated_raw(seed: int, tests: tuple) -> dict:
    data = babel_static(np.random.default_rng(seed))
    if set(tests) <= set(classifier.PERCEPTUAL_TESTS):
        from . import perceptual
        rgb = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float64)
        r = perceptual.raw(rgb)
    else:
        r = raw_scores(data)
    return {t: r[t] for t in tests}


def calibrate_simulated(out: Path = CALIBRATION_FILE, frames: int = 6000, seed: int = 20261107,
                        tests: list[str] | None = None) -> dict:
    """Null maps from simulated static for tests that have none yet (new
    tests, before the scan log has enough real frames for them)."""
    data = json.loads(out.read_text()) if out.exists() else {}
    maps = data.get("maps") or {}
    tests = tuple(tests or [t for t in TESTS if t not in maps])
    if not tests:
        return {}
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 1) as ex:
        rows = list(ex.map(functools.partial(_simulated_raw, tests=tests),
                           range(seed, seed + frames), chunksize=16))
    sources = data.get("map_sources") or {}
    report = {}
    for t in tests:
        v = np.array([r[t] for r in rows])
        maps[t] = nullmap.fit(v)
        sources[t] = {"source": "simulated", "frames": frames}
        held = nullmap.sigma_array(maps[t], v[1::2])
        report[t] = _counts(held)
    data["maps"], data["map_sources"] = maps, sources
    out.write_text(_compact_json(data))
    classifier._calibration_cache = None
    return report


def refit_live(data_dir: Path) -> bool:
    """For the scan workflow: once the score log has enough real frames for a
    test still calibrated on simulated static, or ``REFIT_GROWTH`` times the
    frames a test was last fitted on (a first fit from 5,000 frames has a
    rough tail), refit from the log into ``<data_dir>/calibration.json`` (a
    live layer over the package's calibration, picked up by the next scan,
    which then re-mines). Returns True if it wrote a new live calibration."""
    log_path = data_dir / "scores.npy"
    if not log_path.exists():
        return False
    effective = classifier.live_calibration_path(data_dir) or CALIBRATION_FILE
    data = json.loads(effective.read_text())
    sources = data.get("map_sources") or {}
    raw = raw_from_log(np.load(log_path), data.get("tests") or {})
    def due(t):
        src = sources.get(t, {})
        if src.get("source") != "real":
            return True
        return int(np.isfinite(raw[t]).sum()) >= REFIT_GROWTH * max(src.get("frames", 0), 1)
    pending = [t for t in ready_tests(raw) if due(t)]
    if not pending:
        return False
    live = data_dir / "calibration.json"
    calibrate_from_log(log_path, live, base=effective)
    d = json.loads(live.read_text())
    d["base"] = classifier.package_calibration_hash()
    live.write_text(_compact_json(d))
    print(f"live calibration refitted from the scan log ({len(np.load(log_path)):,} frames); "
          f"due: {', '.join(pending)}")
    return True


def _compact_json(data) -> str:
    """Indented JSON with number lists on one line (the maps' knots)."""
    import re
    text = json.dumps(data, indent=1)
    return re.sub(r"\[\s*(-?[\d.e+-]+(?:,\s*-?[\d.e+-]+)*)\s*\]",
                  lambda m: "[" + re.sub(r"\s+", " ", m.group(1)) + "]", text) + "\n"


def format_report(rep: dict) -> str:
    lines = [f"{'test':22s} {'sd':>5s} {'>2σ':>11s} {'>3σ':>11s} {'>4σ':>9s}   (before: sd, >3σ)"]
    for t, v in rep["validation"].items():
        b = rep["before"].get(t)
        lines.append(f"{t:22s} {v['sd']:5.2f} {v['over_2']:5d}/{v['expected_2']:<5.0f} "
                     f"{v['over_3']:5d}/{v['expected_3']:<5.1f} {v['over_4']:3d}/{v['expected_4']:<5.2f}"
                     + (f"   ({b['sd']:.2f}, {b['over_3']})" if b else ""))
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
