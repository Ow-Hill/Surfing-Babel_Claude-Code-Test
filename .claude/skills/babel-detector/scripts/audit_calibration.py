#!/usr/bin/env python3
"""Audit the detector's calibration on real frames: every test should look
like N(0, 1) on Babel static. This is the check that found the std-floor bug
(9 of 10 tests had sd 0.3-0.7, so nothing ever reached 4 sigma).

    git fetch origin gh-pages
    git show origin/gh-pages:data/scores.npy > /tmp/scores.npy   # or a scratchpad path
    python3 .claude/skills/babel-detector/scripts/audit_calibration.py /tmp/scores.npy

Scores every logged frame from its raw statistics with the *current* calibration
(package, or --calibration PATH for a live data/calibration.json). Read-only.
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from babel_surfer import calibrate, classifier  # noqa: E402
from babel_surfer.classifier import TESTS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scores", type=Path)
    ap.add_argument("--calibration", type=Path, help="e.g. a live data/calibration.json")
    args = ap.parse_args()
    if not args.calibration:            # the site's live layer, if it sits next to the log
        side = args.scores.with_name("calibration.json")
        if side.exists():
            args.calibration = side
    if args.calibration:
        classifier.use_calibration(args.calibration)
    cal = classifier.load_calibration()
    sources = json.loads(classifier.CALIBRATION_FILE.read_text()).get("map_sources") or {}
    log = np.load(args.scores)
    raw = calibrate.raw_from_log(log, cal["tests"])
    z, comb = calibrate.apply_maps(cal["maps"], raw, cal["combined_tests"])
    print(f"{len(log):,} frames, calibration {classifier.CALIBRATION_FILE.name}\n")
    print(f"{'test':10s} {'source':10s} {'n':>7s} {'sd':>5s} {'>2σ obs/exp':>13s} {'>3σ':>11s} {'>4σ':>10s}")
    rows = [(t, z[t]) for t in TESTS] + [("combined", comb)]
    for t, v in rows:
        c = calibrate._counts(v) if np.isfinite(v).any() else None
        src = sources.get(t, {}).get("source", "real" if t in classifier.CORE_TESTS else "?")
        if not c:
            print(f"{t:10s} {src:10s} {'0':>7s}   (no logged values yet)")
            continue
        floored = bool((cal["maps"].get(t) or {}).get("atom"))     # clipped at 0: sd < 1 by design
        flag = "" if floored else ("  <-- check" if abs(c["sd"] - 1) > 0.15 else "")
        src += "*" if floored else ""
        print(f"{t:10s} {src:10s} {c['n']:>7,} {c['sd']:5.2f} {c['over_2']:>6}/{c['expected_2']:<6.0f}"
              f" {c['over_3']:>4}/{c['expected_3']:<6.1f} {c['over_4']:>3}/{c['expected_4']:<6.2f}{flag}")
    present = [t for t in TESTS if np.isfinite(z[t]).any()]
    with np.errstate(all="ignore"):
        peak = np.nanmax(np.stack([z[t] for t in present]), axis=0)
    c = calibrate._counts(peak, of=len(present))
    print(f"\nheadline (max of {len(present)} tests): >3σ {c['over_3']}/{c['expected_3']:.1f}, "
          f">4σ {c['over_4']}/{c['expected_4']:.2f} expected for static")
    print("A test is suspect if its sd is far from 1 or its >3σ count is far from the expectation "
          "(more than ~2-3 Poisson sd). Tests sitting at a floor (contrast, repetition) have sd < 1 "
          "by design (marked *); judge them by their upper tail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
