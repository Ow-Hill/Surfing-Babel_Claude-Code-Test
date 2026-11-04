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
import logging
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .classifier import INTERESTING_SIGMA, classify
from .storage import Store

log = logging.getLogger("babel_surfer.scan")

BASE = "https://babelia.libraryofbabel.info"
ENDPOINT = BASE + "/babelia.cgi"
SLIDESHOW_MAX = 9007199250000000          # same range the slideshow starts in
USER_AGENT = ("SurfingBabel/1.0 (+https://github.com/Ow-Hill/"
              "Surfing-Babel_Claude-Code-Test)")


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
    """Thread-safe 'next location' counter, like the slideshow's postdata++."""

    def __init__(self, start: int):
        self._n = start
        self._lock = threading.Lock()

    def next(self) -> str:
        with self._lock:
            self._n += 1
            return str(self._n)


def scan(out: str, minutes: float, workers: int = 3, start: int | None = None,
         threshold: float = INTERESTING_SIGMA, max_frames: int | None = None) -> dict:
    store = Store(out)
    promoted = store.promote(threshold)
    if promoted:
        log.info("promoted %d earlier near misses to finds (threshold %.1fσ)",
                 promoted, threshold)
    start = random.randrange(SLIDESHOW_MAX) if start is None else start
    counter = Location(start)
    run = {"started_at": time.time(), "start_location": str(start + 1),
           "scanned": 0, "finds": 0, "errors": 0, "workers": workers}
    deadline = time.time() + minutes * 60
    stop = threading.Event()

    def worker():
        while not stop.is_set() and time.time() < deadline:
            if max_frames is not None and run["scanned"] + run["errors"] >= max_frames:
                break
            loc = counter.next()
            try:
                data = fetch(loc)
            except Exception as e:
                log.warning("%s", e)
                store.error(run)
                continue
            result = classify(data, threshold=threshold)
            kind = store.record(loc, data, result, run)
            if kind == "find":
                log.info("FIND  location=%s  %.1fσ (%s, %s)", loc, result.sigma,
                         result.label, result.reason)
                store.save()
            else:
                log.info("frame %s  %.2fσ %s", loc[:6] + "…" + loc[-4:], result.sigma,
                         "(new near miss)" if kind == "near" else "")
            if run["scanned"] % 25 == 0:
                store.save()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        stop.set()
        for t in threads:
            t.join(150)
    run["finished_at"] = time.time()
    run["end_location"] = counter.next()
    store.add_run(run)
    store.save()
    return run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="public/data", help="data directory")
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--workers", type=int, default=3,
                    help="parallel requests (be kind to the server)")
    ap.add_argument("--start", type=int, help="start location (default: random)")
    ap.add_argument("--max-frames", type=int)
    ap.add_argument("--threshold", type=float, default=INTERESTING_SIGMA)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run = scan(args.out, args.minutes, args.workers, args.start, args.threshold,
               args.max_frames)
    print(f"scanned {run['scanned']} frames, {run['finds']} finds, "
          f"{run['errors']} errors, best {run.get('max_sigma', 0):.2f}σ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
