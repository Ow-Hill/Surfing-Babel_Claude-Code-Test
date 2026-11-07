"""Re-mine the score log after a recalibration.

Every scanned frame's raw test statistics are kept in ``data/scores.npy``.
When the calibration changes, frames that were scanned long ago can be
re-scored from those numbers alone, without fetching anything. The few that
now reach the find threshold (or a leaderboard, or the near-miss list) are
fetched again from Babelia (the location regenerates the exact frame),
re-classified and kept the usual way.

Runs at the start of every scan (``scan.scan``) and does nothing unless the
calibration changed since the log was last scored.

    python -m babel_surfer.remine --out public/data
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import calibrate, classifier
from .classifier import INTERESTING_SIGMA, TESTS
from .storage import (HIST_EDGES, LEADERS_KEEP, NEAR_MISS_KEEP, TEST_HIST_BINS, Store,
                      hist_bin, test_hist_bin)

log = logging.getLogger("babel_surfer.remine")

MAX_REFETCH = 60            # a polite cap on how many frames one re-mine fetches
TOLERANCE = 0.05            # fetched score vs score recomputed from the log


def rescore_log(store: Store) -> tuple[np.ndarray, np.ndarray] | None:
    """Recompute every logged frame's scores under the current calibration.
    Rewrites the log (and the histograms built from it) only if anything
    changed. Returns (log, headline sigma per frame) when it did."""
    path = store.dir / "scores.npy"
    cal = classifier.load_calibration()
    if not path.exists() or not cal["maps"]:
        return None
    old = np.load(path)
    if not len(old):
        return None
    scores = Store.upgrade_score_log(old)
    raw = calibrate.raw_from_log(scores, cal["tests"])
    z, comb = calibrate.apply_maps(cal["maps"], raw, cal["combined_tests"])
    new = scores.copy()
    for t in TESTS:
        new[t] = z[t]
        new[f"raw_{t}"] = raw[t]
    new["combined"] = comb
    unchanged = old.dtype == new.dtype and all(
        np.array_equal(old[c], new[c], equal_nan=True) for c in new.dtype.names)
    if unchanged:
        return None
    tmp = store.dir / "scores.tmp.npy"
    np.save(tmp, new)
    os.replace(tmp, path)

    with np.errstate(all="ignore"):
        peak = np.nanmax(np.stack([new[t].astype(float) for t in TESTS]), axis=0)
    with store._lock:
        counts = [0] * (len(HIST_EDGES) + 1)
        for v in peak:
            counts[hist_bin(float(v))] += 1
        store.stats["hist_current"] = {"detector": classifier.detector_id(), "since": None,
                                       "edges": HIST_EDGES, "counts": counts}
        th = {}
        for c in [*TESTS, "combined"]:
            bins = [0] * TEST_HIST_BINS
            for v in new[c].astype(float):
                if np.isfinite(v):
                    bins[test_hist_bin(float(v))] += 1
            th[c] = bins
        store.stats["test_hist"]["counts"] = th
        store.stats["test_hist"]["detector"] = classifier.detector_id()
        best = int(np.nanargmax(peak))
        store.stats["best"] = {"location": str(int(new["location"][best])),
                               "sigma": round(float(peak[best]), 3)}
    return new, peak


def candidates(scores: np.ndarray, peak: np.ndarray, threshold: float,
               known: set[str]) -> list[tuple[str, float]]:
    """Frames worth fetching again: every one at/above the threshold, the
    best near misses and each test's leaders. Highest first."""
    pick: dict[int, float] = {}
    order = np.argsort(-peak)
    for i in order[peak[order] >= threshold]:
        pick[int(i)] = float(peak[i])
    for i in order[:NEAR_MISS_KEEP]:
        pick.setdefault(int(i), float(peak[i]))
    for c in [*TESTS, "combined"]:
        col = np.nan_to_num(scores[c].astype(float), nan=-np.inf)
        for i in np.argsort(-col)[:LEADERS_KEEP]:
            pick.setdefault(int(i), float(peak[i]))
    out = [(str(int(scores["location"][i])), s) for i, s in pick.items()]
    out = [o for o in out if o[0] not in known]
    out.sort(key=lambda o: -o[1])
    return out[:MAX_REFETCH]


def mine(store: Store, threshold: float = INTERESTING_SIGMA, workers: int = 2,
         fetch=None, classify=classifier.classify) -> dict | None:
    """Re-score the log; fetch and keep frames that now qualify. Returns a
    run summary (also added to the run history) or None if nothing changed."""
    res = rescore_log(store)
    if res is None:
        return None
    scores, peak = res
    if fetch is None:
        from .scan import fetch
    todo = candidates(scores, peak, threshold, store._referenced())
    log.info("re-mined %d logged frames: %d at/above %.1fσ, fetching %d candidates",
             len(scores), int((peak >= threshold).sum()), threshold, len(todo))
    run = {"started_at": time.time(), "trigger": "remine", "scanned": 0, "finds": 0,
           "errors": 0, "remined": int(len(scores)), "refetched": 0, "mismatches": 0}

    def one(item):
        loc, expected = item
        try:
            data = fetch(loc)
        except Exception as e:
            log.warning("could not fetch %s: %s", loc, e)
            store.error(run)
            return
        result = classify(data, threshold=threshold)
        run["refetched"] += 1
        if abs(result.sigma - expected) > TOLERANCE:
            # Babelia's frame is the truth; the log was float16 or an older code path
            run["mismatches"] += 1
            log.warning("%s: logged %.2fσ, fetched frame scores %.2fσ", loc, expected, result.sigma)
        kind = store.record(loc, data, result, run, count=False)
        if kind == "find":
            log.info("FIND (re-mined) location=%s  %.1fσ (%s)", loc, result.sigma, result.reason)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(one, todo))
    run["finished_at"] = time.time()
    run["max_sigma"] = round(float(np.nanmax(peak)), 3)
    store.add_run(run, primary=False)
    store.save()
    return run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="public/data")
    ap.add_argument("--threshold", type=float, default=INTERESTING_SIGMA)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    classifier.activate_live_calibration(args.out)
    run = mine(Store(args.out), args.threshold, args.workers)
    if run is None:
        print("score log already matches the current calibration")
    else:
        print(f"re-mined {run['remined']} frames, re-fetched {run['refetched']}, "
              f"{run['finds']} new finds, {run['mismatches']} score mismatches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
