"""JSON + image-file storage, laid out so a static website can read it.

    <data_dir>/stats.json        totals, sigma histogram, recent runs
    <data_dir>/finds.json        the top MAX_FINDS frames at/above the threshold
    <data_dir>/near_misses.json  the highest-scoring frames below the threshold
    <data_dir>/images/<location>.jpg       320x208 previews (~28 KB)
    <data_dir>/images/<location>.heat.png  where the structure is (~200 B)

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

from .classifier import heatmap_png

NEAR_MISS_KEEP = 12
MAX_FINDS = 100
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
        self.stats = _read(self.dir / "stats.json", {})
        self.stats.setdefault("scanned", 0)
        self.stats.setdefault("errors", 0)
        self.stats.setdefault("hist_edges", HIST_EDGES)
        self.stats.setdefault("hist", [0] * (len(HIST_EDGES) + 1))
        self.stats.setdefault("runs", [])
        self.finds = _read(self.dir / "finds.json", [])
        self.near = _read(self.dir / "near_misses.json", [])
        self.stats.setdefault("finds_total", len(self.finds))
        self._tidy()

    # ------------------------------------------------------------------
    def _img(self, location: str) -> Path:
        return self.image_dir / f"{location}.jpg"

    def _heat(self, location: str) -> Path:
        return self.image_dir / f"{location}.heat.png"

    def _save_image(self, location: str, image: bytes) -> None:
        self._img(location).write_bytes(make_thumb(image))
        # computed from the full frame: previews lose static's statistics
        self._heat(location).write_bytes(heatmap_png(image))

    def _drop_image(self, location: str) -> None:
        self._img(location).unlink(missing_ok=True)
        self._heat(location).unlink(missing_ok=True)

    def _referenced(self) -> set[str]:
        return {e["location"] for e in self.finds} | {e["location"] for e in self.near}

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

    def _tidy(self) -> None:
        """Migrate older data: shrink full-size images, apply the finds cap
        and delete images nothing refers to."""
        self._cap_finds()
        keep = self._referenced()
        for p in self.image_dir.glob("*.heat.png"):
            if p.name[: -len(".heat.png")] not in keep:
                p.unlink()
        for p in self.image_dir.glob("*.jpg"):
            if p.stem not in keep:
                p.unlink()
            elif p.stat().st_size > THUMB_MAX_BYTES:
                # an old full-size frame: make its heatmap before shrinking it
                full = p.read_bytes()
                if not self._heat(p.stem).exists():
                    self._heat(p.stem).write_bytes(heatmap_png(full))
                p.write_bytes(make_thumb(full))
        for e in self.finds + self.near:
            e["heat"] = (f"images/{e['location']}.heat.png"
                         if self._heat(e["location"]).exists() else None)

    # ------------------------------------------------------------------
    def record(self, location: str, image: bytes, result, run: dict) -> str | None:
        """Account for one classified frame. Returns 'find', 'near' or None."""
        entry = {"location": location, "sigma": result.sigma, "tier": result.label,
                 "reason": result.reason, "tests": result.tests,
                 "found_at": time.time(), "image": f"images/{location}.jpg",
                 "heat": f"images/{location}.heat.png"}
        with self._lock:
            self.stats["scanned"] += 1
            self.stats["hist"][hist_bin(result.sigma)] += 1
            run["scanned"] += 1
            run["max_sigma"] = max(run.get("max_sigma", float("-inf")), result.sigma)
            best = self.stats.get("best")
            if best is None or result.sigma > best["sigma"]:
                self.stats["best"] = {"location": location, "sigma": result.sigma}

            if result.is_interesting:
                if not any(f["location"] == location for f in self.finds):
                    self.stats["finds_total"] += 1
                    run["finds"] += 1
                    lowest = min((f["sigma"] for f in self.finds), default=float("-inf"))
                    if len(self.finds) >= MAX_FINDS and result.sigma <= lowest:
                        return "find"      # counted, but not in the top MAX_FINDS
                    self._save_image(location, image)
                    self.finds.append(entry)
                    run.setdefault("new_finds", []).append(
                        {"location": location, "sigma": result.sigma, "tier": result.label,
                         "reason": result.reason})
                    self._cap_finds()
                    return "find"
                return None

            lowest = min((n["sigma"] for n in self.near), default=float("-inf"))
            if len(self.near) < NEAR_MISS_KEEP or result.sigma > lowest:
                if any(n["location"] == location for n in self.near):
                    return None
                self._save_image(location, image)
                self.near.append(entry)
                self.near.sort(key=lambda n: n["sigma"], reverse=True)
                for gone in self.near[NEAR_MISS_KEEP:]:
                    if not any(f["location"] == gone["location"] for f in self.finds):
                        self._drop_image(gone["location"])
                del self.near[NEAR_MISS_KEEP:]
                return "near"
        return None

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

    def add_run(self, run: dict) -> None:
        with self._lock:
            self.stats["runs"] = (self.stats["runs"] + [run])[-RUN_HISTORY:]
            self.stats["last_run"] = run

    def save(self) -> None:
        with self._lock:
            self.stats["updated_at"] = time.time()
            _write(self.dir / "stats.json", self.stats)
            _write(self.dir / "finds.json",
                   sorted(self.finds, key=lambda f: f["found_at"], reverse=True))
            _write(self.dir / "near_misses.json", self.near)
