"""Seeded demos: proof that a real picture in Babel would be caught.

Random slideshow frames essentially never contain a picture, so to show the
pipeline end to end we go the other way:

1. draw a picture ourselves (no licensing questions),
2. upload it to Babelia's own image search (``/imagesearch.cgi``), which
   answers with the picture's *location* in the archive (~960,000 digits:
   real pictures live at astronomically large locations, which is why the
   slideshow, starting below 10^16, never meets one),
3. fetch that location back from ``/babelia.cgi`` exactly as the scanner
   does, and score it with the same detector.

The results are published as clearly labelled demos, never as finds.

    python -m babel_surfer.seeded --out public/demo
"""

from __future__ import annotations

import argparse
import io
import json
import re
import urllib.request
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from . import benchmark
from .calibrate import LEVELS
from . import analysis
from .classifier import classify
from .scan import BASE, USER_AGENT, fetch

SEARCH = BASE + "/imagesearch.cgi"


def _landscape() -> Image.Image:
    return benchmark.pic_landscape()[0]


def _faint_disc() -> Image.Image:
    rng = np.random.default_rng(20261104)
    picture, _ = benchmark.pic_disc()
    noise = LEVELS[rng.integers(0, 16, (benchmark.H, benchmark.W, 3))].astype(float)
    mixed = 0.95 * noise + 0.05 * np.asarray(picture, dtype=float)
    return Image.fromarray(LEVELS[np.clip(np.rint(mixed / 17), 0, 15).astype(int)].astype(np.uint8))


DEMOS = [
    {"slug": "landscape", "make": _landscape, "title": "A drawn landscape",
     "description": "A simple picture: about as obvious as a find could be."},
    {"slug": "faint-disc", "make": _faint_disc, "title": "A red disc hidden in static (5%)",
     "description": "Babel static with a red disc blended in at 5% strength. "
                    "Hard to see by eye; the detector still flags it."},
]


def search_location(png: bytes, filename: str) -> str:
    """Upload a picture to Babelia's image search; return its location."""
    boundary = uuid.uuid4().hex
    fields = {"MAX_FILE_SIZE": "15728640", "flip": "l", "fill": "s", "frame": "1",
              "color": "#222", "colortext": ""}
    body = io.BytesIO()
    for k, v in fields.items():
        body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="title"; '
               f'filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'.encode())
    body.write(png + b"\r\n" + f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(SEARCH, data=body.getvalue(), method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": USER_AGENT, "Referer": BASE + "/imagesearch.html"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        html = resp.read().decode("utf-8", "replace")
    m = re.search(r'id\s*=\s*"location"\s+value\s*=\s*"(\d+)"', html)
    if not m:
        raise RuntimeError("Babelia's image search returned no location")
    return m.group(1)


def generate(out: Path, demo: dict) -> None:
    """Network step: picture -> location -> frame fetched back from Babel."""
    buf = io.BytesIO()
    demo["make"]().save(buf, "PNG")
    loc = search_location(buf.getvalue(), f"{demo['slug']}.png")
    frame = fetch(loc, timeout=180)
    (out / f"{demo['slug']}.location.txt").write_text(loc)
    (out / f"{demo['slug']}.jpg").write_bytes(frame)


def ensure(out: Path, network: bool = True) -> list[dict]:
    """Make sure every demo exists (fetching if needed), then (re)score them
    and write ``demo.json``. Scoring is redone every build so demos always
    reflect the current detector."""
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    for demo in DEMOS:
        jpg = out / f"{demo['slug']}.jpg"
        loc_file = out / f"{demo['slug']}.location.txt"
        if not (jpg.exists() and loc_file.exists()):
            if not network:
                continue
            try:
                generate(out, demo)
            except Exception as e:          # Babelia down: skip, retry next build
                print(f"demo {demo['slug']}: {e}")
                continue
        frame = jpg.read_bytes()
        loc = loc_file.read_text().strip()
        c = classify(frame)
        maps = analysis.analyze(frame)
        (out / f"{demo['slug']}.heat.png").unlink(missing_ok=True)     # superseded
        public = {k: v for k, v in maps.items() if k not in analysis.SERVER_ONLY}
        (out / f"{demo['slug']}.maps.json").write_text(json.dumps(public, separators=(",", ":")))
        mask = None
        if c.reason == "tone" or c.tests.get("tone", 0) >= 3:
            (out / f"{demo['slug']}.mask.png").write_bytes(analysis.tone_mask_png(frame, maps))
            mask = f"demo/{demo['slug']}.mask.png"
        entries.append({
            "slug": demo["slug"], "title": demo["title"], "blurb": demo["description"],
            "sigma": c.sigma, "tier": c.label, "reason": c.reason, "tests": c.tests,
            "combined": c.combined,
            "image": f"demo/{demo['slug']}.jpg", "maps": f"demo/{demo['slug']}.maps.json",
            "mask": mask, "description": analysis.describe(c.tests, maps, c.sigma),
            "location_file": f"demo/{demo['slug']}.location.txt",
            "location_digits": len(loc), "location_preview": f"{loc[:12]}…{loc[-12:]}",
        })
    (out / "demo.json").write_text(json.dumps(entries, indent=1))
    return entries


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("public/demo"))
    args = ap.parse_args(argv)
    for e in ensure(args.out):
        print(f"{e['slug']}: {e['sigma']:.1f}σ ({e['tier']}, {e['reason']}), "
              f"location has {e['location_digits']:,} digits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
