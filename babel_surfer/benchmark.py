"""How well does the detector work? Hide known signals in Babel static.

Each case builds a picture, blends it into simulated Babel static at some
strength (alpha), snaps to Babel's 16-level palette, JPEG-encodes it exactly
like Babelia does and classifies it. Also measures the false-positive rate on
pure static. Writes ``benchmark.json`` (+ example images for the website).

    python -m babel_surfer.benchmark --trials 10 --null 2000 --out public/benchmark
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .calibrate import LEVELS, encode_like_babelia
from .classifier import INTERESTING_SIGMA, classify

W, H = 640, 416


def _canvas(bg=(128, 128, 128)):
    img = Image.new("RGB", (W, H), bg)
    return img, ImageDraw.Draw(img)


def pic_disc():
    img, d = _canvas()
    d.ellipse((200, 88, 440, 328), fill=(220, 40, 40))
    return img, None


def pic_square():
    img, d = _canvas()
    d.rectangle((300, 196, 324, 220), fill=(0, 0, 0))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rectangle((300, 196, 324, 220), fill=255)
    return img, mask          # only the square replaces noise


def pic_line():
    img, d = _canvas()
    d.rectangle((0, 250, W, 251), fill=(255, 255, 255))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rectangle((0, 250, W, 251), fill=255)
    return img, mask


def pic_text():
    img, d = _canvas((0, 0, 0))
    try:
        font = ImageFont.load_default(size=110)
    except TypeError:      # Pillow < 10.1
        font = ImageFont.load_default()
    d.text((70, 140), "BABEL", fill=(255, 255, 255), font=font)
    return img, None


def pic_landscape():
    img, d = _canvas()
    for y in range(H):        # sky gradient
        c = int(90 + 120 * y / H)
        d.line((0, y, W, y), fill=(c // 2, int(c / 1.5), c))
    d.polygon([(0, 300), (160, 180), (330, 290), (480, 160), (640, 280), (640, 416), (0, 416)],
              fill=(40, 90, 40))
    d.ellipse((500, 40, 570, 110), fill=(250, 220, 90))
    return img, None


SIGNALS = {
    "large disc":            (pic_disc, (0.02, 0.05, 0.1, 1.0)),
    "landscape":             (pic_landscape, (0.02, 0.05, 0.1, 1.0)),
    "word 'BABEL'":          (pic_text, (0.02, 0.05, 0.1, 1.0)),
    "horizon line (2px)":    (pic_line, (0.1, 0.25, 0.5, 1.0)),
    "small square (24px)":   (pic_square, (0.1, 0.25, 0.5, 1.0)),
}


def _blend(rng, picture: Image.Image, mask: Image.Image | None, alpha: float) -> bytes:
    noise = LEVELS[rng.integers(0, 16, (H, W, 3))].astype(float)
    pic = np.asarray(picture, dtype=float)
    a = alpha * (np.asarray(mask, dtype=float)[..., None] / 255 if mask else 1.0)
    mixed = (1 - a) * noise + a * pic
    snapped = LEVELS[np.clip(np.rint(mixed / 17), 0, 15).astype(int)]
    return encode_like_babelia(snapped)


def _special(rng, kind: str) -> bytes:
    if kind == "greyscale static":
        g = LEVELS[rng.integers(0, 16, (H, W))]
        return encode_like_babelia(np.stack([g, g, g], axis=-1))
    if kind == "dark static (half palette)":
        return encode_like_babelia(LEVELS[rng.integers(0, 8, (H, W, 3))])
    if kind == "1px checkerboard + static":
        noise = LEVELS[rng.integers(0, 16, (H, W, 3))].astype(float)
        yy, xx = np.indices((H, W))
        board = ((xx + yy) % 2 * 255.0)[..., None]
        mixed = 0.8 * noise + 0.2 * board
        return encode_like_babelia(LEVELS[np.clip(np.rint(mixed / 17), 0, 15).astype(int)])
    raise KeyError(kind)


def _snap(mixed: np.ndarray) -> bytes:
    return encode_like_babelia(LEVELS[np.clip(np.rint(mixed / 17), 0, 15).astype(int)])


def _noise(rng) -> np.ndarray:
    return LEVELS[rng.integers(0, 16, (H, W, 3))].astype(float)


def _grey_patch(rng, alpha: float) -> bytes:
    """A 64 px patch pulled toward mid-grey: its average matches the frame,
    so only its lack of contrast gives it away."""
    x = _noise(rng)
    sl = (slice(176, 240), slice(288, 352))
    x[sl] = (1 - alpha) * x[sl] + alpha * 127.5
    return _snap(x)


def _diagonal_line(rng, alpha: float) -> bytes:
    pic = Image.new("RGB", (W, H), (128, 128, 128))
    mask = Image.new("L", (W, H), 0)
    for im, fill in ((pic, (255, 255, 255)), (mask, 255)):
        ImageDraw.Draw(im).line((40, 400, 600, 77), fill=fill, width=2)   # ~30 degrees
    return _blend(rng, pic, mask, alpha)


def _pick(rng, alpha: float, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Each pixel from ``b`` with probability alpha, else from ``a``. Unlike
    blending, this keeps static's flat colour histogram, so only the
    structure being tested is present."""
    take = rng.random((H, W, 1)) < alpha
    return np.where(take, b, a)


def _tiled(rng, alpha: float) -> bytes:
    """Static in which a share of pixels repeats every 160 x 104 px."""
    tiled = np.tile(_noise(rng)[:104, :160], (4, 4, 1))
    return _snap(_pick(rng, alpha, _noise(rng), tiled))


def _mirrored(rng, alpha: float) -> bytes:
    """Static in which a share of pixels mirrors the left half."""
    x = _noise(rng)
    m = x.copy()
    m[:, W // 2:] = x[:, : W // 2][:, ::-1]
    return _snap(_pick(rng, alpha, _noise(rng), m))


NEW_SIGNALS = {
    "grey patch (64px)": (_grey_patch, (0.25, 0.5, 1.0)),
    "diagonal line (2px, 30°)": (_diagonal_line, (0.25, 0.5, 1.0)),
    "tiled static": (_tiled, (0.1, 0.2, 0.4)),
    "mirror-symmetric static": (_mirrored, (0.1, 0.2, 0.4)),
}
SPECIALS = ("greyscale static", "dark static (half palette)", "1px checkerboard + static")


def _classify_many(frames: list[bytes]) -> list:
    workers = max(1, os.cpu_count() or 1)
    if workers == 1 or len(frames) < 4:
        return [classify(f) for f in frames]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(classify, frames, chunksize=4))


def run(trials: int, null_frames: int, out: Path | None, seed: int = 7) -> dict:
    from .calibrate import babel_static
    rng = np.random.default_rng(seed)

    # 1. build every frame (cheap), 2. classify them all in parallel
    cases = []          # (name, strength, [frames])
    examples = []
    for name, (maker, alphas) in SIGNALS.items():
        picture, mask = maker()
        for a in alphas:
            frames = [_blend(rng, picture, mask, a) for _ in range(trials)]
            cases.append((name, a, frames))
            if out is not None and a in (alphas[1], alphas[-1]):
                examples.append((f"{name} @ {a:g}", frames[0]))
    for name, (maker, alphas) in NEW_SIGNALS.items():
        for a in alphas:
            frames = [maker(rng, a) for _ in range(trials)]
            cases.append((name, a, frames))
            if out is not None and a == alphas[-1]:
                examples.append((f"{name} @ {a:g}", frames[0]))
    for kind in SPECIALS:
        frames = [_special(rng, kind) for _ in range(trials)]
        cases.append((kind, "-", frames))
        if out is not None:
            examples.append((kind, frames[0]))
    null_frames_b = [babel_static(rng) for _ in range(null_frames)]

    flat = [f for _, _, fr in cases for f in fr] + null_frames_b + [d for _, d in examples]
    results = _classify_many(flat)
    pos = 0
    rows = []
    for name, strength, frames in cases:
        res = results[pos:pos + len(frames)]
        pos += len(frames)
        sig = [r.sigma for r in res]
        rows.append({
            "signal": name, "strength": strength,
            "min_sigma": round(min(sig), 2), "median_sigma": round(statistics.median(sig), 2),
            "detected": sum(r.is_interesting for r in res), "trials": len(res),
            "reason": statistics.mode(r.reason for r in res),
        })
        print(f"  {name:28s} {strength!s:>6}  min {min(sig):8.1f}σ  "
              f"detected {rows[-1]['detected']}/{len(res)}  ({rows[-1]['reason']})", file=sys.stderr)
    null = results[pos:pos + null_frames]
    pos += null_frames
    ex_res = results[pos:]

    null_sig = [c.sigma for c in null]
    from .storage import HIST_EDGES, hist_bin
    null_hist = [0] * (len(HIST_EDGES) + 1)
    for v in null_sig:
        null_hist[hist_bin(v)] += 1
    result = {
        "threshold_sigma": INTERESTING_SIGMA,
        "null": {"frames": null_frames, "max_sigma": round(max(null_sig), 2),
                 "hist_edges": HIST_EDGES, "hist": null_hist,
                 "mean_sigma": round(statistics.fmean(null_sig), 2),
                 "false_positives": sum(c.is_interesting for c in null),
                 "worst_test": max(null, key=lambda c: c.sigma).reason},
        "signals": rows,
    }
    print(f"  pure static: max {result['null']['max_sigma']}σ over {null_frames}, "
          f"false positives {result['null']['false_positives']}", file=sys.stderr)

    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        ex = []
        for i, ((label, data), c) in enumerate(zip(examples, ex_res)):
            fn = f"example_{i:02d}.jpg"
            (out / fn).write_bytes(data)
            ex.append({"label": label, "file": fn, "sigma": c.sigma,
                       "tier": c.label, "reason": c.reason})
        result["examples"] = ex
        (out / "benchmark.json").write_text(json.dumps(result, indent=2))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--null", type=int, default=500)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    run(args.trials, args.null, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
