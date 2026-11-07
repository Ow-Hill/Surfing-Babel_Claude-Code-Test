"""JSON + image-file storage, laid out so a static website can read it.

    <data_dir>/stats.json        totals, sigma histogram, recent runs
    <data_dir>/finds.json        the top MAX_FINDS frames at/above the threshold
    <data_dir>/near_misses.json  the highest-scoring frames below the threshold
    <data_dir>/images/<location>.jpg       320x208 previews (~28 KB)
    <data_dir>/images/<location>.maps.json evidence maps for the overlay (~16 KB)
    <data_dir>/images/<location>.mask.png  pixels in the most deviant colour
                                           range (colour-balance finds only)

Only small previews are stored: a full Babelia frame is ~460 KB, and the
location (seed) regenerates it exactly at babelia.cgi?location=N, which the
website loads on demand. Finds are capped at the MAX_FINDS highest scores so
the published site stays well under GitHub Pages' 1 GB limit.
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
from pathlib import Path

from PIL import Image

from . import analysis
from .classifier import TESTS, detector_id


def assets_id() -> str:
    """Hash of the code that turns a frame into its saved maps, description
    and visibility. When it changes, saved frames are rebuilt on the next scan
    (without touching the score histograms, which follow detector_id)."""
    import hashlib
    h = hashlib.sha256()
    pkg = Path(__file__).parent
    for name in ("analysis.py", "visibility.py", "perceptual.py"):
        h.update((pkg / name).read_bytes())
    return h.hexdigest()[:12]

NEAR_MISS_KEEP = 12
LEADERS_KEEP = 3                 # top frames kept per test (and for "combined")
SCORE_LOG_KEEP = 100_000         # rolling log of every frame's scores
# per-test score histograms: 0.25-sigma bins from -5 to 20, plus under/overflow
TEST_HIST_LO, TEST_HIST_HI, TEST_HIST_STEP = -5.0, 20.0, 0.25
TEST_HIST_BINS = int((TEST_HIST_HI - TEST_HIST_LO) / TEST_HIST_STEP) + 2
MAX_FINDS = 100
MAX_FLAGS = 50                  # frames people flagged ("People noticed")
THUMB_SIZE = (320, 208)
THUMB_QUALITY = 75
THUMB_MAX_BYTES = 100_000      # anything bigger is a full frame -> shrink it
# sigma histogram bin edges; the last bin is "everything above"
HIST_EDGES = [-2, -1, 0, 1, 2, 3, 4, 5, 6, 8, 12, 16, 40]
RUN_HISTORY = 60


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def make_thumb(image: bytes) -> bytes:
    """Downscale a frame to a small JPEG preview (box filter: averages, so a
    genuine pattern stays visible)."""
    im = Image.open(io.BytesIO(image)).convert("RGB")
    im = im.resize(THUMB_SIZE, Image.BOX)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=THUMB_QUALITY, optimize=True)
    return buf.getvalue()


def test_hist_bin(z: float) -> int:
    if z < TEST_HIST_LO:
        return 0
    if z >= TEST_HIST_HI:
        return TEST_HIST_BINS - 1
    return 1 + int((z - TEST_HIST_LO) / TEST_HIST_STEP)


def hist_bin(sigma: float) -> int:
    for i in range(len(HIST_EDGES) - 1, -1, -1):
        if sigma >= HIST_EDGES[i]:
            return i + 1
    return 0


class Store:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir)
        self.image_dir = self.dir / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()      # one save at a time (score log is read-modify-write)
        self.stats = _read(self.dir / "stats.json", {})
        self.stats.setdefault("scanned", 0)
        self.stats.setdefault("errors", 0)
        self.stats.setdefault("hist_edges", HIST_EDGES)
        self.stats.setdefault("hist", [0] * (len(HIST_EDGES) + 1))
        self.stats.setdefault("runs", [])
        self.finds = _read(self.dir / "finds.json", [])
        self.near = _read(self.dir / "near_misses.json", [])
        self.leaders = _read(self.dir / "leaders.json", {})
        self.flags = _read(self.dir / "flags.json", [])       # frames people flagged
        self.stats.setdefault("finds_total", len(self.finds))
        self.stats.setdefault("test_hist", {"lo": TEST_HIST_LO, "step": TEST_HIST_STEP, "counts": {}})
        self.stats.setdefault("fingerprint", {"n": 0, "sum": 0.0, "sumsq": 0.0, "min": None})
        self._score_log: list[tuple] = []
        self._detector = detector_id()
        self._assets = assets_id()
        self._init_current_hist()
        self._tidy()

    # ------------------------------------------------------------------
    def _img(self, location: str) -> Path:
        return self.image_dir / f"{location}.jpg"

    def _maps(self, location: str) -> Path:
        return self.image_dir / f"{location}.maps.json"

    def _mask(self, location: str) -> Path:
        return self.image_dir / f"{location}.mask.png"

    def _write_assets(self, location: str, image: bytes, result) -> dict:
        """Preview, evidence maps (from the full frame: previews lose
        static's statistics), tone mask and description. Slow (~1.5 s), so
        callers run it outside the lock."""
        self._img(location).write_bytes(make_thumb(image))
        maps = analysis.analyze(image)
        description = analysis.describe(result.tests, maps, result.sigma)
        # the per-test eye maps only feed the description; the site draws the merged map
        public = {k: v for k, v in maps.items() if k not in analysis.SERVER_ONLY}
        self._maps(location).write_text(json.dumps(public, separators=(",", ":")))
        fields = {"maps": f"images/{location}.maps.json", "mask": None,
                  "description": description,
                  "detector": self._detector, "assets": self._assets,
                  "visibility": maps.get("visibility")}
        if result.reason == "tone" or result.tests.get("tone", 0) >= 3:
            self._mask(location).write_bytes(analysis.tone_mask_png(image, maps))
            fields["mask"] = f"images/{location}.mask.png"
        return fields

    def _drop_image(self, location: str) -> None:
        for p in (self._img(location), self._maps(location), self._mask(location),
                  self.image_dir / f"{location}.heat.png"):
            p.unlink(missing_ok=True)

    def missing_assets(self) -> list[dict]:
        """Entries that need their full frame again (Babelia regenerates it
        from the location): saved before evidence maps existed, or scored by
        a different detector version."""
        current = self._detector
        seen, out = set(), []
        for e in self._all_entries():
            stale = (not self._maps(e["location"]).exists() or e.get("detector") != current
                     or e.get("assets") != self._assets)
            if e["location"] not in seen and stale:
                seen.add(e["location"])
                out.append(e)
        return out

    def reconcile(self, threshold: float) -> int:
        """After re-scoring: drop finds that no longer reach the threshold,
        re-rank near misses and leaderboards, delete files nothing uses.
        Returns how many finds were dropped."""
        from .classifier import tier_for
        with self._lock:
            keep = [f for f in self.finds if f["sigma"] >= threshold]
            dropped = len(self.finds) - len(keep)
            # a near miss or leader whose new score reaches the threshold becomes a find
            boards = [e for b in self.leaders.values() for e in b]
            for n in self.near + boards:
                if n["sigma"] >= threshold and not any(f["location"] == n["location"] for f in keep):
                    n["tier"] = tier_for(n["sigma"])
                    keep.append(n)
                    n.setdefault("found_at", time.time())
                    self.stats["finds_total"] = self.stats.get("finds_total", 0) + 1
            self.finds = keep
            self._cap_finds()
            self.stats["finds_total"] = max(len(keep), self.stats.get("finds_total", 0) - dropped)
            self.near = sorted([n for n in self.near if n["sigma"] < threshold],
                               key=lambda n: n["sigma"], reverse=True)[:NEAR_MISS_KEEP]
            for t, board in self.leaders.items():
                key = (lambda e, t=t: e["tests"].get(t, 0) if t != "combined" else e.get("combined", 0))
                board.sort(key=key, reverse=True)
                del board[LEADERS_KEEP:]
            refs = self._referenced()
            for p in list(self.image_dir.iterdir()):
                if p.name.split(".", 1)[0] not in refs:
                    p.unlink()
        return dropped

    def enrich(self, entry: dict, image: bytes, result) -> None:
        """Rebuild an entry's assets and scores from its full frame."""
        fields = self._write_assets(entry["location"], image, result)
        with self._lock:
            for e in self._all_entries():          # the same frame may sit on several lists
                if e["location"] == entry["location"]:
                    e.update(fields, sigma=result.sigma, tests=result.tests, reason=result.reason,
                             tier=result.label, combined=result.combined,
                             sigma_global=getattr(result, "sigma_global", None))

    def _referenced(self) -> set[str]:
        refs = {e["location"] for e in self.finds} | {e["location"] for e in self.near}
        refs |= {e["location"] for e in self.flags}
        for board in self.leaders.values():
            refs |= {e["location"] for e in board}
        return refs

    def _all_entries(self) -> list[dict]:
        out = self.finds + self.near + self.flags
        for board in self.leaders.values():
            out += board
        return out

    def _cap_finds(self) -> None:
        if len(self.finds) <= MAX_FINDS:
            return
        self.finds.sort(key=lambda f: f["sigma"], reverse=True)
        dropped = self.finds[MAX_FINDS:]
        del self.finds[MAX_FINDS:]
        keep = self._referenced()
        for f in dropped:
            if f["location"] not in keep:
                self._drop_image(f["location"])

    def _init_current_hist(self) -> None:
        """Histogram of frame scores under the *current* detector only. The
        all-time ``hist`` mixes frames scored by older detector versions;
        this one restarts whenever the detector changes. First time: rebuilt
        from the score log (which only ever held current-format scores)."""
        cur = self.stats.get("hist_current")
        if cur and cur.get("detector") == self._detector:
            return
        counts = [0] * (len(HIST_EDGES) + 1)
        since = time.time()
        if cur is None:                                  # migration: backfill from the log
            try:
                import numpy as np
                log = np.load(self.dir / "scores.npy")
                cols = [c for c in self.SCORE_COLUMNS[1:-2] if c in log.dtype.names]
                if len(log) and cols:
                    peak = np.nanmax(np.stack([log[c].astype(float) for c in cols]), axis=0)
                    for v in peak:
                        counts[hist_bin(float(v))] += 1
                    since = None                         # unknown: "since the score log began"
            except (FileNotFoundError, ValueError, OSError):
                pass
        self.stats["hist_current"] = {"detector": self._detector, "since": since,
                                      "edges": HIST_EDGES, "counts": counts}

    def _tidy(self) -> None:
        """Migrate older data: shrink full-size images, apply the finds cap
        and delete images nothing refers to."""
        self._cap_finds()
        keep = self._referenced()
        for p in self.image_dir.glob("*.heat.png"):      # replaced by maps.json
            p.unlink()
        for p in self.image_dir.iterdir():
            loc = p.name.split(".", 1)[0]
            if loc not in keep:
                p.unlink()
            elif p.suffix == ".jpg" and p.stat().st_size > THUMB_MAX_BYTES:
                p.write_bytes(make_thumb(p.read_bytes()))   # old full-size frame
        for e in self._all_entries():
            e.pop("heat", None)
            if not self._maps(e["location"]).exists():
                e["maps"] = None

    # ------------------------------------------------------------------
    def record(self, location: str, image: bytes, result, run: dict,
               count: bool = True) -> str | None:
        """Account for one classified frame. Returns 'find', 'near' or None.
        ``count=False`` for a frame that was already counted when it was first
        scanned (re-mining): it can be kept, but totals and logs don't change."""
        entry = {"location": location, "sigma": result.sigma, "tier": result.label,
                 "reason": result.reason, "tests": result.tests,
                 "combined": getattr(result, "combined", 0.0),
                 "sigma_global": getattr(result, "sigma_global", None),
                 "found_at": time.time(), "image": f"images/{location}.jpg",
                 "maps": None, "mask": None, "description": None}
        kind = self._account(location, entry, result, run, count)
        leader = self._board(location, entry, result)
        if kind == "over_cap":            # counted as a find but not stored
            kind = "find"
            stored = leader
        else:
            stored = bool(kind or leader)
        if stored:
            fields = self._write_assets(location, image, result)
            with self._lock:
                entry.update(fields)
                # pushed off every list while its assets were being written?
                if location not in self._referenced():
                    self._drop_image(location)
        return kind or ("leader" if leader else None)

    def _board(self, location: str, entry: dict, result) -> bool:
        """Per-test leaderboards: the top frames for each test (and for the
        combined score), so the site shows every kind of anomaly, not just
        the test that fires most."""
        scores = dict(result.tests)
        scores["combined"] = getattr(result, "combined", 0.0)
        placed = False
        with self._lock:
            for test, z in scores.items():
                board = self.leaders.setdefault(test, [])
                key = (lambda e, t=test: e["tests"].get(t, 0) if t != "combined" else e.get("combined", 0))
                if len(board) >= LEADERS_KEEP and z <= min(key(e) for e in board):
                    continue
                if any(e["location"] == location for e in board):
                    continue
                board.append(entry)
                board.sort(key=key, reverse=True)
                gone = board[LEADERS_KEEP:]
                del board[LEADERS_KEEP:]
                placed = placed or entry in board
                refs = self._referenced()
                for g in gone:
                    if g["location"] not in refs:
                        self._drop_image(g["location"])
        return placed

    def _account(self, location: str, entry: dict, result, run: dict,
                 count: bool = True) -> str | None:
        """Bookkeeping under the lock; decides whether the frame is kept."""
        with self._lock:
            if count:
                self._count(location, result, run)
            return self._keep(location, entry, result, run, count)

    def _count(self, location: str, result, run: dict) -> None:
        self.stats["scanned"] += 1
        self.stats["hist"][hist_bin(result.sigma)] += 1
        self.stats["hist_current"]["counts"][hist_bin(result.sigma)] += 1
        # every frame's scores: per-test histograms (kept forever, tiny) and a
        # rolling raw log (for recalibration and research)
        counts = self.stats["test_hist"]["counts"]
        combined = getattr(result, "combined", 0.0)
        fp = getattr(result, "fingerprint", 0.0)
        for t, z in list(result.tests.items()) + [("combined", combined)]:
            counts.setdefault(t, [0] * TEST_HIST_BINS)[test_hist_bin(z)] += 1
        f = self.stats["fingerprint"]
        f["n"] += 1
        f["sum"] += fp
        f["sumsq"] += fp * fp
        if f["min"] is None or fp < f["min"]["value"]:
            f["min"] = {"value": fp, "location": location}
        try:
            raw = getattr(result, "raw", None) or {}
            self._score_log.append((int(location),
                                    *[result.tests.get(t, float("nan")) for t in self.SCORE_COLUMNS[1:-2]],
                                    combined, fp,
                                    *[raw.get(t, float("nan")) for t in self.SCORE_COLUMNS[1:-2]]))
        except ValueError:
            pass                                   # non-numeric location (tests)
        run["scanned"] += 1
        run["max_sigma"] = max(run.get("max_sigma", float("-inf")), result.sigma)
        best = self.stats.get("best")
        if best is None or result.sigma > best["sigma"]:
            self.stats["best"] = {"location": location, "sigma": result.sigma}

    def _keep(self, location: str, entry: dict, result, run: dict,
              count: bool = True) -> str | None:
        """Returns 'find', 'near', 'over_cap' (a find counted but not stored:
        below the top MAX_FINDS) or None. With ``count=False`` (a frame seen
        before, e.g. re-mined), only a newly stored find adds to the total, so
        re-mining the same frames never inflates it."""
        if result.is_interesting:
            if not any(f["location"] == location for f in self.finds):
                lowest = min((f["sigma"] for f in self.finds), default=float("-inf"))
                if len(self.finds) >= MAX_FINDS and result.sigma <= lowest:
                    if count:
                        self.stats["finds_total"] += 1
                        run["finds"] += 1
                    return "over_cap"
                self.stats["finds_total"] += 1
                run["finds"] += 1
                self.finds.append(entry)
                run.setdefault("new_finds", []).append(entry)
                self._cap_finds()
                return "find"
            return None

        lowest = min((n["sigma"] for n in self.near), default=float("-inf"))
        if len(self.near) < NEAR_MISS_KEEP or result.sigma > lowest:
            if any(n["location"] == location for n in self.near):
                return None
            self.near.append(entry)
            self.near.sort(key=lambda n: n["sigma"], reverse=True)
            gone = self.near[NEAR_MISS_KEEP:]
            del self.near[NEAR_MISS_KEEP:]
            refs = self._referenced()
            for g in gone:
                if g["location"] not in refs:      # may still be a find or a leader
                    self._drop_image(g["location"])
            return "near" if entry in self.near else None
        return None

    def add_flag(self, location: str, image: bytes, result, votes: int, note: str = "",
                 url: str | None = None, run: dict | None = None) -> dict:
        """A frame a person flagged: always kept (with preview, maps and
        description) in the "People noticed" list, ranked by votes, and also
        accounted as a find / near miss / leader if it qualifies."""
        kept = self.record(location, image, result, run, count=False) if run is not None else None
        fields = None
        if kept:                                   # record() already wrote fresh assets
            with self._lock:
                prev = next((e for e in self._all_entries()
                             if e["location"] == location and e.get("maps")), None)
            if prev:
                fields = {k: prev.get(k) for k in ("maps", "mask", "description", "detector", "assets",
                                                   "visibility")}
        if fields is None:
            fields = self._write_assets(location, image, result)
        entry = {"location": location, "sigma": result.sigma, "tier": result.label,
                 "reason": result.reason, "tests": result.tests,
                 "combined": getattr(result, "combined", 0.0),
                 "sigma_global": getattr(result, "sigma_global", None),
                 "image": f"images/{location}.jpg", "votes": int(votes), "note": note[:280],
                 "issue": url, "found_at": time.time(), **fields}
        with self._lock:
            old = next((f for f in self.flags if f["location"] == location), None)
            if old:
                entry["votes"] = max(entry["votes"], old.get("votes", 0))
                self.flags.remove(old)
            self.flags.append(entry)
            self.flags.sort(key=lambda f: (f.get("votes", 0), (f.get("visibility") or {}).get("ratio", 0),
                                           f["sigma"]), reverse=True)
            gone = self.flags[MAX_FLAGS:]
            del self.flags[MAX_FLAGS:]
            refs = self._referenced()
            for g in gone:
                if g["location"] not in refs:
                    self._drop_image(g["location"])
        return entry

    def promote(self, threshold: float) -> int:
        """Move stored near misses at/above ``threshold`` into finds (used
        when the threshold is lowered). Returns how many moved."""
        from .classifier import tier_for
        with self._lock:
            keep, moved = [], 0
            for n in self.near:
                if n["sigma"] >= threshold:
                    if not any(f["location"] == n["location"] for f in self.finds):
                        self.finds.append({**n, "tier": tier_for(n["sigma"])})
                        self.stats["finds_total"] += 1
                        moved += 1
                else:
                    keep.append(n)
            self.near = keep
            self._cap_finds()
        return moved

    def error(self, run: dict) -> None:
        with self._lock:
            self.stats["errors"] += 1
            run["errors"] += 1

    def add_run(self, run: dict, primary: bool = True) -> None:
        """Record a run. ``primary=False`` for side jobs (re-mining, flags):
        they go into the history but don't replace ``last_run`` (the site's
        "last scan" and the frame counts refer to real scans)."""
        with self._lock:
            # keep run history small: summaries of new finds, not full entries
            run["new_finds"] = [{k: f.get(k) for k in ("location", "sigma", "tier", "reason", "description")}
                                for f in run.get("new_finds", [])]
            self.stats["runs"] = (self.stats["runs"] + [run])[-RUN_HISTORY:]
            if primary:
                self.stats["last_run"] = run

    def save(self) -> None:
        with self._save_lock:
            self._save()

    def _save(self) -> None:
        with self._lock:
            self.stats["updated_at"] = time.time()
            _write(self.dir / "stats.json", self.stats)
            _write(self.dir / "finds.json",
                   sorted(self.finds, key=lambda f: f["found_at"], reverse=True))
            _write(self.dir / "near_misses.json", self.near)
            _write(self.dir / "flags.json", self.flags)
            _write(self.dir / "leaders.json", self.leaders)
            log, self._score_log = self._score_log, []
        if log:
            self._append_score_log(log)

    SCORE_COLUMNS = ["location", *sorted(TESTS), "combined", "fingerprint"]

    @classmethod
    def score_log_dtype(cls):
        """location (uint64), calibrated scores (float16) and the raw test
        statistics (float32, ``raw_<test>``): 72 bytes per frame."""
        return ([("location", "<u8")] + [(c, "<f2") for c in cls.SCORE_COLUMNS[1:]]
                + [(f"raw_{c}", "<f4") for c in cls.SCORE_COLUMNS[1:-2]])

    @classmethod
    def upgrade_score_log(cls, old):
        """Copy an older-format log into the current format (missing columns
        become NaN), so a format change never throws history away."""
        import numpy as np
        dtype = np.dtype(cls.score_log_dtype())
        if old.dtype == dtype:
            return old
        new = np.zeros(len(old), dtype=dtype)
        for name in dtype.names:
            if name in old.dtype.names:
                new[name] = old[name]
            elif dtype[name].kind == "f":
                new[name] = np.nan
        return new

    def _append_score_log(self, rows: list[tuple]) -> None:
        """Rolling binary log of the last SCORE_LOG_KEEP frames' scores."""
        import numpy as np
        new = np.array(rows, dtype=self.score_log_dtype())
        path = self.dir / "scores.npy"
        try:
            new = np.concatenate([self.upgrade_score_log(np.load(path)), new])
        except (FileNotFoundError, ValueError, OSError):
            pass
        tmp = self.dir / "scores.tmp.npy"
        np.save(tmp, new[-SCORE_LOG_KEEP:])
        os.replace(tmp, path)
