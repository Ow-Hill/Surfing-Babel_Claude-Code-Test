"""Blind-spot probe: how faint can each kind of signal be before the detector
misses it, and does a person see it first?

For every signal family (the benchmark's, plus shapes the tests were not
designed around) the probe searches the strength axis by geometric bisection:
at each step it hides the signal in fresh simulated static (Babelia's exact
JPEG), classifies a handful of frames, and moves toward the strength where
half are found. It reports, per family:

* ``detect``: the weakest strength found in at least half the trials (null if
  even full strength is missed);
* ``by``: which tests catch it at that strength;
* ``eye``: the weakest strength the visibility model rates "likely visible";
* ``blind``: the eye sees it at a strength the detector misses. These are
  the detector's blind spots, the ones worth a new or better test.

It never touches Babelia, so it can run anywhere. ``--explore N`` adds N
random variants (shape, size, colour, position) drawn from a seed; missed
variants that a person would see go in the result's ``misses``, so repeated
runs keep collecting the cases the detector gets wrong.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import benchmark as B
from .calibrate import LEVELS

W, H = B.W, B.H
LO, HI = 0.01, 1.0


# --- signal families: f(rng, strength in (0, 1]) -> JPEG bytes ---------------

def _mask_blend(rng, pic: np.ndarray, mask: np.ndarray, a: float) -> bytes:
    """Blend picture into static where mask (0..1) is set, at strength a."""
    noise = B._noise(rng)
    m = a * mask[..., None]
    return B._snap((1 - m) * noise + m * pic)


def _draw(shape, fill=(255, 255, 255), bg=(128, 128, 128)):
    pic = Image.new("RGB", (W, H), bg)
    mask = Image.new("L", (W, H), 0)
    shape(ImageDraw.Draw(pic), fill)
    shape(ImageDraw.Draw(mask), 255)
    return np.asarray(pic, float), np.asarray(mask, float) / 255


def _picture(maker):
    pic, mask = maker()
    p = np.asarray(pic, float)
    m = np.ones((H, W)) if mask is None else np.asarray(mask, float) / 255
    return lambda rng, a: _mask_blend(rng, p, m, a)


def _shape(shape, fill=(255, 255, 255)):
    p, m = _draw(shape, fill)
    return lambda rng, a: _mask_blend(rng, p, m, a)


def _text_small():
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:      # Pillow < 10.1
        font = ImageFont.load_default()
    return _shape(lambda d, f: d.text((200, 190), "THE LIBRARY", fill=f, font=font))


def _gradient(rng, a):
    ramp = np.linspace(0, 255, W)[None, :, None] * np.ones((H, 1, 3))
    return _mask_blend(rng, ramp, np.ones((H, W)), a)


def _stripes(rng, a):
    g = 127.5 + 127.5 * np.sin(np.arange(H) * 2 * math.pi / 8)[:, None, None] * np.ones((1, W, 3))
    return _mask_blend(rng, g, np.ones((H, W)), a)


def _soft_blob(rng, a):
    yy, xx = np.mgrid[0:H, 0:W]
    m = np.exp(-((xx - 320) ** 2 + (yy - 208) ** 2) / (2 * 50.0 ** 2))
    return _mask_blend(rng, np.full((H, W, 3), (230.0, 60.0, 60.0)), m, a)


def _one_channel(rng, a):
    """A 64 px patch where the red channel alone is pinned high."""
    x = B._noise(rng)
    sl = (slice(176, 240), slice(288, 352))
    take = rng.random((64, 64)) < a
    x[sl][..., 0][take] = 255.0
    return B._snap(x)


def _coarse_levels(rng, a):
    """A 96 px patch where a share of pixels use 4 levels per channel, not 16."""
    x = B._noise(rng)
    sl = (slice(160, 256), slice(272, 368))
    coarse = LEVELS[rng.integers(0, 4, (96, 96, 3)) * 5].astype(float)
    take = rng.random((96, 96, 1)) < a
    x[sl] = np.where(take, coarse, x[sl])
    return B._snap(x)


def _dither(rng, a):
    """A 96 px patch of a 2 px black-and-white checker."""
    yy, xx = np.mgrid[0:H, 0:W]
    board = (((xx // 2 + yy // 2) % 2) * 255.0)[..., None] * np.ones((1, 1, 3))
    m = np.zeros((H, W))
    m[160:256, 272:368] = 1
    return _mask_blend(rng, board, m, a)


FAMILIES = {
    # the benchmark's pictures and structures
    "large disc": _picture(B.pic_disc),
    "landscape": _picture(B.pic_landscape),
    "word 'BABEL'": _picture(B.pic_text),
    "horizon line (2px)": _picture(B.pic_line),
    "small square (24px)": _picture(B.pic_square),
    "thin arc (3px)": B._arc,
    "pen stroke (2px)": B._stroke,
    "face-like spots": B._face,
    "few-colour patch (64px)": B._posterised,
    "grey patch (64px)": B._grey_patch,
    "diagonal line (2px, 30°)": B._diagonal_line,
    "tiled static": B._tiled,
    "mirror-symmetric static": B._mirrored,
    # shapes the tests were not designed around
    "small dot (8px)": _shape(lambda d, f: d.rectangle((316, 204, 323, 211), fill=f), (0, 0, 0)),
    "outline box (1px)": _shape(lambda d, f: d.rectangle((240, 150, 400, 260), outline=f, width=1)),
    "rings (2px)": _shape(lambda d, f: [d.ellipse((320 - r, 208 - r, 320 + r, 208 + r), outline=f, width=2)
                                       for r in (30, 60, 90, 120)]),
    "small text (18px)": _text_small(),
    "soft colour blob": _soft_blob,
    "brightness gradient": _gradient,
    "stripes (8px period)": _stripes,
    "one-channel patch (64px)": _one_channel,
    "coarse-level patch (96px)": _coarse_levels,
    "2px checker patch (96px)": _dither,
}


# --- random variants for exploration ------------------------------------------

def variant(seed: int):
    """A random signal: a drawn shape of random kind, size, colour, width and
    place. Deterministic in ``seed``; returns (description, maker)."""
    r = np.random.default_rng(seed)
    kind = ["line", "box", "outline", "ellipse", "arc", "polyline"][int(r.integers(6))]
    col = tuple(int(c) for c in LEVELS[r.integers(0, 16, 3)])
    size = int(r.choice([8, 16, 32, 64, 128]))
    width = int(r.choice([1, 2, 3, 4]))
    x0, y0 = int(r.integers(20, W - 20 - size)), int(r.integers(20, H - 20 - size))
    ang = float(r.uniform(0, math.pi))
    pts = [(x0 + size * i / 8, y0 + size / 2 + size / 3 * math.sin(i + ang)) for i in range(9)]
    box = (x0, y0, x0 + size, y0 + size)

    def shape(d, f):
        if kind == "line":
            d.line((x0, y0, x0 + size * 4 * math.cos(ang), y0 + size * 4 * math.sin(ang)), fill=f, width=width)
        elif kind == "box":
            d.rectangle(box, fill=f)
        elif kind == "outline":
            d.rectangle(box, outline=f, width=width)
        elif kind == "ellipse":
            d.ellipse(box, fill=f)
        elif kind == "arc":
            d.arc(box, 0, 270, fill=f, width=width)
        else:
            d.line(pts, fill=f, width=width)
    desc = f"{kind} {size}px w{width} rgb{col} at ({x0},{y0})"
    return desc, _shape(shape, col)


# --- the probe ------------------------------------------------------------------

def _make(job):
    """Worker: build one frame and score it (tests >= 4 sigma, frame sigma)."""
    family, seed, strength = job
    maker = FAMILIES[family] if family in FAMILIES else variant(int(family.split("#")[1]))[1]
    data = maker(np.random.default_rng(seed), strength)
    from .classifier import INTERESTING_SIGMA, classify
    c = classify(data)
    return {t for t, z in c.tests.items() if z >= INTERESTING_SIGMA}, c.sigma


def _eye(job):
    family, seed, strength = job
    maker = FAMILIES[family] if family in FAMILIES else variant(int(family.split("#")[1]))[1]
    from .visibility import visibility
    rgb = np.asarray(Image.open(io.BytesIO(maker(np.random.default_rng(seed), strength))).convert("RGB"),
                     dtype=np.float64)
    return visibility(rgb)["ratio"]


def _bisect(names, trials, steps, pool, score, seed0):
    """Geometric bisection per family of the strength where ``score`` of a
    trial batch first passes; returns {name: (strength or None, samples)}."""
    lo = {n: LO for n in names}
    hi = {n: HI for n in names}
    samples = {n: [] for n in names}

    def run(points):
        jobs = [(n, seed0 + 1000 * k + i, s) for k, (n, s) in enumerate(points) for i in range(trials)]
        res = list(pool.map(score[0], jobs, chunksize=2))
        for k, (n, s) in enumerate(points):
            samples[n].append((s, res[k * trials:(k + 1) * trials]))
            yield n, s, score[1](res[k * trials:(k + 1) * trials])

    found = {n: ok for n, _, ok in run([(n, HI) for n in names])}
    live = [n for n in names if found[n]]
    for step in range(steps):
        pts = [(n, math.sqrt(lo[n] * hi[n])) for n in live]
        for n, s, ok in run(pts):
            (hi if ok else lo)[n] = s
        seed0 += 7919
    return {n: (round(hi[n], 4) if found[n] else None, samples[n]) for n in names}


def run(trials: int = 6, steps: int = 6, explore: int = 0, seed: int = 0, families=None,
        workers: int | None = None, calibration: Path | None = None) -> dict:
    from . import classifier
    if calibration:
        classifier.use_calibration(str(calibration))
    names = list(families or FAMILIES)
    variants = [f"variant#{seed * 100000 + i}" for i in range(explore)]
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count() or 1,
                             initializer=classifier.use_calibration,
                             initargs=(str(classifier.CALIBRATION_FILE),)) as pool:
        det = _bisect(names + variants, trials, steps, pool,
                      (_make, lambda r: sum(bool(t) for t, _ in r) * 2 >= len(r)), seed * 7 + 1)
        eye = _bisect(names + variants, 1, steps, pool,
                      (_eye, lambda r: r[0] >= 2.0), seed * 7 + 3)
        # the eye's rating at the strength the detector needs
        at = [(n, seed * 7 + 5, det[n][0]) for n in names + variants if det[n][0]]
        eye_at = dict(zip([a[0] for a in at], pool.map(_eye, at)))
    rows, misses = [], []
    for n in names + variants:
        s, smp = det[n]
        by = {}
        for st, res in smp:
            if s and abs(st - s) < 1e-9:
                for tests, _ in res:
                    for t in tests:
                        by[t] = by.get(t, 0) + 1
        e = eye[n][0]
        row = {"signal": n if n in FAMILIES else variant(int(n.split("#")[1]))[0],
               "detect": s, "eye": e,
               "by": sorted(by, key=by.get, reverse=True)[:3],
               "eye_at_detect": round(eye_at[n], 2) if n in eye_at else None,
               "blind": e is not None and (s is None or e < s)}
        (rows if n in FAMILIES else misses).append(row)
    misses = [m for m in misses if m["blind"]]
    return {"generated": int(time.time()), "seconds": round(time.time() - t0),
            "trials": trials, "steps": steps, "seed": seed, "threshold_sigma": classifier.INTERESTING_SIGMA,
            "families": rows, "explored": explore, "misses": misses,
            "summary": {"families": len(rows), "blind": sum(r["blind"] for r in rows),
                        "never_detected": sum(r["detect"] is None for r in rows)}}


def merge_history(old: dict | None, new: dict, keep: int = 60) -> dict:
    """Carry forward what earlier runs learned: a per-run summary line, and the
    union of explored misses (newest first, deduplicated by description)."""
    hist = (old or {}).get("history", [])
    hist.append({"generated": new["generated"], **new["summary"], "explored": new["explored"],
                 "new_misses": len(new["misses"])})
    seen, misses = set(), []
    for m in new["misses"] + (old or {}).get("misses", []):
        if m["signal"] not in seen:
            seen.add(m["signal"])
            misses.append(m)
    return {**new, "history": hist[-keep:], "misses": misses[:keep]}


def report(res: dict) -> str:
    out = [f"{'signal':28s} {'detected from':>13s} {'eye from':>9s} {'eye there':>9s}  caught by"]
    for r in res["families"]:
        d = f"{r['detect']:.3f}" if r["detect"] else "missed"
        e = f"{r['eye']:.3f}" if r["eye"] else "never"
        ea = f"{r['eye_at_detect']:.2f}" if r["eye_at_detect"] is not None else "-"
        out.append(f"{r['signal'][:28]:28s} {d:>13s} {e:>9s} {ea:>9s}  {', '.join(r['by'])}"
                   + ("   << BLIND SPOT" if r["blind"] else ""))
    s = res["summary"]
    out.append(f"{s['blind']} of {s['families']} families are seen by eye before the detector finds them; "
               f"{s['never_detected']} are never detected. Explored {res['explored']} random variants: "
               f"{len(res['misses'])} visible misses.")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials", type=int, default=6)
    ap.add_argument("--steps", type=int, default=6)
    ap.add_argument("--explore", type=int, default=0, help="random variants to try")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--family", action="append", help="only these families")
    ap.add_argument("--calibration", type=Path, help="calibration.json to score with (default: package)")
    ap.add_argument("--out", type=Path, help="write (and merge history into) this JSON file")
    a = ap.parse_args(argv)
    res = run(a.trials, a.steps, a.explore, a.seed, a.family, calibration=a.calibration)
    if a.out:
        old = json.loads(a.out.read_text()) if a.out.exists() else None
        res = merge_history(old, res)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=1))
    print(report(res), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
