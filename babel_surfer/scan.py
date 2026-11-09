"""Surf the Babelia slideshow and keep anything that isn't static.

This does what https://babelia.libraryofbabel.info/slideshow.html does in
the browser (see its ``/js/slideshow.js``): pick a random location below
9,007,199,250,000,000, then POST ``location=N`` to ``/babelia.cgi`` for
N, N+1, N+2, ... Each response is a 640x416 JPEG; the location number *is*
the image's seed. Every frame is classified, and finds / near misses are
saved with their location.

    python -m babel_surfer.scan --out public/data --minutes 20 --workers 3
"""

from __future__ import annotations

import argparse
import io
import logging
import math
import multiprocessing
import os
import random
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from PIL import Image

from . import classifier
from .calibrate import BABELIA_QTABLES
from .classifier import INTERESTING_SIGMA, TESTS, classify
from .storage import Store

log = logging.getLogger("babel_surfer.scan")

BASE = "https://babelia.libraryofbabel.info"
ENDPOINT = BASE + "/babelia.cgi"
SLIDESHOW_MAX = 9007199250000000          # same range the slideshow starts in
USER_AGENT = ("SurfingBabel/1.0 (+https://github.com/Ow-Hill/"
              "Surfing-Babel_Claude-Code-Test)")


FRAME_SIZE = (640, 416)
# Guard against a runaway find rate (a broken calibration, or Babelia changing
# its frames): halt the scan when a run's finds exceed what static gives by
# this many Poisson standard deviations (plus a margin for small runs).
P_FIND = 1 - (1 - 3.167e-5) ** len(TESTS)         # P(any test >= 4 sigma) on static
HALT_SD, HALT_MARGIN = 6.0, 4


def frame_problem(data: bytes) -> str | None:
    """Why these bytes are not a genuine Babelia frame, or None. Every real
    frame is a 640x416 RGB baseline JPEG encoded with Babelia's own
    quantisation tables; an error page, a resized or re-encoded copy (a proxy
    or cache) or a damaged download is not, and would score as a striking
    "find" (a grey error image reads 60,000 sigma)."""
    try:
        im = Image.open(io.BytesIO(data))
        if im.format != "JPEG":
            return f"not a JPEG ({im.format})"
        if im.size != FRAME_SIZE or im.mode != "RGB":
            return f"{im.size[0]}x{im.size[1]} {im.mode}, not 640x416 RGB"
        q = getattr(im, "quantization", None) or {}
        if sorted(q) != list(range(len(BABELIA_QTABLES))) or \
                any(list(q[i]) != list(t) for i, t in enumerate(BABELIA_QTABLES)):
            return "not encoded with Babelia's JPEG tables"
        im.load()                                      # truncated data fails here
    except Exception as e:                             # noqa: BLE001 - any decode failure
        return f"unreadable ({type(e).__name__}: {e})"
    return None


def implausible_finds(finds: int, scanned: int) -> bool:
    """True when this many finds in this many frames can't be static's tail."""
    expected = scanned * P_FIND
    return finds > expected + HALT_SD * math.sqrt(expected) + HALT_MARGIN


def image_url(location: str) -> str:
    """A direct link to the image at this location."""
    return f"{ENDPOINT}?location={location}"


def fetch(location: str, timeout: float = 120.0, retries: int = 3) -> bytes:
    body = urllib.parse.urlencode({"location": location}).encode()
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT, "Referer": BASE + "/slideshow.html"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            if data[:2] != b"\xff\xd8":
                raise ValueError(f"not a JPEG ({len(data)} bytes)")
            return data
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            last = e
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"location {location}: {last}")


class Location:
    """Thread-safe 'next location' counter, like the slideshow's postdata++.
    With ``limit``, hands out at most that many locations (then None)."""

    def __init__(self, start: int, limit: int | None = None):
        self._n = start
        self._left = limit
        self._lock = threading.Lock()

    def next(self) -> str | None:
        with self._lock:
            if self._left is not None:
                if self._left <= 0:
                    return None
                self._left -= 1
            self._n += 1
            return str(self._n)

    def peek(self) -> str:
        with self._lock:
            return str(self._n + 1)


class Politeness:
    """Adaptive concurrency: Babelia is a one-person site, so scale the
    number of simultaneous requests to what it handles without slowing down.

    Tracks fetch latency. The baseline is the best (lowest) rolling median
    seen, relaxing slowly (``DRIFT`` per decision) so one lucky quiet spell
    doesn't make normal latency look slow for the rest of the run. While the
    current median stays under 1.3x baseline another request slot opens (up
    to ``max_workers``); above 2x, or on errors, one closes (down to
    ``min_workers``). After every change the latency window starts afresh, so
    the next decision is judged on requests made at the new level (judging
    on stale samples used to cascade 5 -> 1 in about a minute)."""

    WINDOW = 12
    DRIFT = 1.03

    def __init__(self, start: int, min_workers: int = 1, max_workers: int = 5):
        self.limit = max(min_workers, min(start, max_workers))
        self.min, self.max = min_workers, max_workers
        self.active = 0
        self.baseline: float | None = None
        self.lat: list[float] = []
        self.done = 0
        self.history: list[tuple[float, int]] = [(time.time(), self.limit)]
        self._cv = threading.Condition()

    def acquire(self, stop: threading.Event) -> bool:
        with self._cv:
            while self.active >= self.limit:
                if stop.is_set():
                    return False
                self._cv.wait(1.0)
            self.active += 1
            return True

    def release(self, latency: float | None) -> None:
        with self._cv:
            self.active -= 1
            if latency is None:                          # error: back off now
                self._set(self.limit - 1, "error")
            else:
                self.lat = (self.lat + [latency])[-self.WINDOW:]
                self.done += 1
                if len(self.lat) >= self.WINDOW // 2 and self.done % 4 == 0:
                    med = statistics.median(self.lat)
                    self.baseline = (med if self.baseline is None
                                     else min(self.baseline * self.DRIFT, med))
                    if med > 2.0 * self.baseline:
                        self._set(self.limit - 1, f"slow ({med:.1f}s vs {self.baseline:.1f}s)")
                    elif med < 1.3 * self.baseline:
                        self._set(self.limit + 1, f"fast ({med:.1f}s)")
            self._cv.notify_all()

    def release_unused(self) -> None:
        """Give back a slot that never made a request (no latency to learn from)."""
        with self._cv:
            self.active -= 1
            self._cv.notify_all()

    def _set(self, n: int, why: str) -> None:
        n = max(self.min, min(self.max, n))
        if n != self.limit:
            log.info("concurrency %d -> %d (%s)", self.limit, n, why)
            self.limit = n
            self.lat = []          # judge the new level on its own requests
            self.history.append((time.time(), n))


def scan(out: str, minutes: float, workers: int = 3, start: int | None = None,
         threshold: float = INTERESTING_SIGMA, max_frames: int | None = None,
         max_workers: int | None = None, processes: int | None = None,
         resume: bool = False) -> dict:
    """Scan for ``minutes``. Fetching happens in threads (the server is the
    bottleneck); classification runs in ``processes`` worker processes
    (default: all cores but one; 0 = in the fetch threads), so scoring never
    holds up the next request."""
    # a calibration refitted from this site's own scan log, if there is one
    live = classifier.activate_live_calibration(out)
    if live:
        log.info("using live calibration %s", live)
    store = Store(out)
    if store.stats.get("halted"):
        if not resume:
            log.error("scanning is halted (%s): %s. Run with --resume once it is understood.",
                      time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(store.stats["halted"]["at"])),
                      store.stats["halted"]["reason"])
            return {"scanned": 0, "finds": 0, "errors": 0, "halted": store.stats["halted"]["reason"]}
        log.info("resuming after a halt: %s", store.stats.pop("halted")["reason"])
        store.save()
    promoted = store.promote(threshold)
    if promoted:
        log.info("promoted %d earlier near misses to finds (threshold %.1fσ)",
                 promoted, threshold)
    # Entries saved before evidence maps existed, or scored by an older
    # detector: re-fetch their full frames (Babelia regenerates them from the
    # location), re-score them and rebuild their assets.
    stale = store.missing_assets()

    def rescore(entry):
        try:
            data = fetch(entry["location"])
            store.enrich(entry, data, classify(data, threshold=threshold))
            log.info("re-scored %s", entry["location"])
        except Exception as e:
            log.warning("could not re-score %s: %s", entry["location"], e)
    if stale:
        log.info("re-scoring %d saved frames with the current detector", len(stale))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            list(ex.map(rescore, stale))
    if stale:
        dropped = store.reconcile(threshold)
        if dropped:
            log.info("%d finds no longer reach %.1fσ under the current detector", dropped, threshold)
    # After a recalibration: re-score the whole score log and fetch the
    # frames that now qualify (no-op otherwise).
    from . import remine
    remine.mine(store, threshold, workers=max(1, min(workers, 2)))
    store.save()
    start = random.randrange(SLIDESHOW_MAX) if start is None else start
    counter = Location(start, max_frames)
    max_workers = max(workers, max_workers or workers)
    polite = Politeness(workers, 1, max_workers)
    run = {"started_at": time.time(), "start_location": str(start + 1),
           "scanned": 0, "finds": 0, "errors": 0, "workers": workers, "max_workers": max_workers}
    deadline = time.time() + minutes * 60
    stop = threading.Event()

    if processes is None:
        processes = max(1, (os.cpu_count() or 2) - 1)
    pool = (ProcessPoolExecutor(processes, mp_context=multiprocessing.get_context("forkserver"),
                                initializer=classifier.use_calibration,
                                initargs=(str(classifier.CALIBRATION_FILE),))
            if processes > 0 else None)

    pool_lock = threading.Lock()

    def score(data: bytes):
        nonlocal pool
        p = pool
        if p is None:
            return classify(data, threshold=threshold)
        try:
            return p.submit(classify, data, threshold=threshold).result()
        except BrokenProcessPool:
            # a worker process died (e.g. out of memory): score in this thread
            # from now on rather than throwing every later frame away
            with pool_lock:
                if pool is p:
                    log.warning("classifier process pool broke; scoring in the fetch threads")
                    pool = None
                    p.shutdown(wait=False, cancel_futures=True)
            return classify(data, threshold=threshold)

    def bump(run, key):
        with pool_lock:
            run[key] = run.get(key, 0) + 1

    def confirm(loc: str, data: bytes) -> bytes | None:
        """A second download of ``loc``: ``data`` itself if it matches; if it
        differs, a third download decides (the copy it matches is returned);
        None if no two downloads agree (or a download fails)."""
        for _ in range(2):
            if not polite.acquire(stop):
                return None
            t0 = time.time()
            try:
                again = fetch(loc)
            except Exception as e:
                polite.release(None)
                log.warning("could not re-fetch %s: %s", loc, e)
                return None
            polite.release(time.time() - t0)
            if again == data:
                return data
            if frame_problem(again):
                return None
            data = again
        return None

    def worker():
        while not stop.is_set() and time.time() < deadline:
            if not polite.acquire(stop):
                break
            if time.time() >= deadline:
                polite.release_unused()
                break
            loc = counter.next()
            if loc is None:
                polite.release_unused()
                break
            t0 = time.time()
            try:
                data = fetch(loc)
            except Exception as e:
                polite.release(None)
                log.warning("%s", e)
                store.error(run)
                continue
            polite.release(time.time() - t0)
            problem = frame_problem(data)
            if problem:                 # not a genuine frame: never score it
                log.warning("rejected %s: %s", loc, problem)
                bump(run, "rejected")
                store.error(run)
                continue
            try:
                result = score(data)
            except Exception as e:      # count it, keep scanning
                log.warning("could not score %s: %s", loc, e)
                store.error(run)
                continue
            if result.is_interesting:
                # Babelia renders a location identically every time, so a find
                # must survive a second download byte for byte (a download
                # damaged in transit can't repeat)
                again = confirm(loc, data)
                if again is None:
                    log.warning("unconfirmed find %s (%.1fσ): a second download differed", loc, result.sigma)
                    bump(run, "unconfirmed")
                    store.error(run)
                    continue
                if again is not data:   # the first copy was the damaged one
                    data = again
                    try:
                        result = score(data)
                    except Exception as e:
                        log.warning("could not score %s: %s", loc, e)
                        store.error(run)
                        continue
            try:
                kind = store.record(loc, data, result, run)
                if kind == "find":
                    log.info("FIND  location=%s  %.1fσ (%s, %s)", loc, result.sigma,
                             result.label, result.reason)
                    store.save()
                    if implausible_finds(run["finds"], run["scanned"]):
                        run["halted"] = (f"{run['finds']} finds in {run['scanned']} frames, far more than "
                                         f"static gives ({run['scanned'] * P_FIND:.1f} expected): "
                                         "check the calibration and Babelia's frames before scanning on")
                        log.error("HALT: %s", run["halted"])
                        stop.set()
                else:
                    log.info("frame %s  %.2fσ %s", loc[:6] + "…" + loc[-4:], result.sigma,
                             "(new near miss)" if kind == "near" else "")
                if run["scanned"] % 25 == 0:
                    store.save()
            except Exception as e:      # an analysis or disk error must not end this thread
                log.exception("could not keep %s: %s", loc, e)
                store.error(run)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max_workers)]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        stop.set()
        for t in threads:
            t.join(150)
    if pool is not None:
        pool.shutdown()
    run["finished_at"] = time.time()
    run["end_location"] = counter.peek()
    minutes_run = max((run["finished_at"] - run["started_at"]) / 60, 1e-9)
    run["frames_per_hour"] = round(run["scanned"] / minutes_run * 60)
    run["concurrency"] = {"final": polite.limit, "max_used": max(n for _, n in polite.history),
                          "baseline_latency_s": round(polite.baseline or 0, 2),
                          "changes": len(polite.history) - 1,
                          # (minutes into the run, request slots), to see throughput limits
                          "history": [[round((t - run["started_at"]) / 60, 1), n]
                                      for t, n in polite.history[-40:]]}
    if run.get("halted"):
        # the run's finds are not trusted: off the site, out of the totals and
        # alerts, kept in stats["quarantine"] for a look
        run["quarantined"] = store.quarantine(run, run["halted"])
    store.add_run(run)
    store.save()
    return run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="public/data", help="data directory")
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--workers", type=int, default=3,
                    help="parallel requests to start with (be kind to the server)")
    ap.add_argument("--max-workers", type=int, default=None,
                    help="let adaptive concurrency go up to this many while Babelia stays fast")
    ap.add_argument("--start", type=int, help="start location (default: random)")
    ap.add_argument("--max-frames", type=int)
    ap.add_argument("--threshold", type=float, default=INTERESTING_SIGMA)
    ap.add_argument("--processes", type=int, default=None,
                    help="classifier processes (default: cores - 1; 0 = classify in the fetch threads)")
    ap.add_argument("--resume", action="store_true",
                    help="clear a halt (an implausible find rate) and scan again")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run = scan(args.out, args.minutes, args.workers, args.start, args.threshold,
               args.max_frames, args.max_workers, args.processes, resume=args.resume)
    print(f"scanned {run['scanned']} frames, {run['finds']} finds, "
          f"{run['errors']} errors ({run.get('rejected', 0)} not genuine frames, "
          f"{run.get('unconfirmed', 0)} unconfirmed finds), best {run.get('max_sigma', 0):.2f}σ")
    if run.get("halted"):
        print(f"HALTED: {run['halted']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
