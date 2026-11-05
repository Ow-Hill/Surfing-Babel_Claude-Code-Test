"""Measure the detector's null distribution and write ``calibration.json``.

Babelia frames are 640x416, every pixel an independent uniformly random
12-bit colour (16 levels per channel), JPEG-encoded (quality ~90, no chroma
subsampling). We simulate exactly that with Babelia's own quantization tables, run every test, and record the
mean / std of each raw statistic. Optionally, real frames downloaded from
Babelia are scored with the result to confirm simulation and reality agree.

    python -m babel_surfer.calibrate --frames 400 --real-dir path/to/jpgs
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

from . import classifier
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
    args = ap.parse_args(argv)

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

    tests = {t: {"mean": statistics.fmean(v), "std": max(statistics.pstdev(v), 1.0),
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

    result = {"frames": args.frames, "model": "uniform 16-level RGB, JPEG with Babelia qtables, 4:4:4",
              "tone_source": tone_source,
              "tests": tests, "tone_expected": tone_expected.round(6).tolist()}

    args.out.write_text(json.dumps(result, indent=2) + "\n")
    classifier._calibration_cache = None
    print(f"wrote {args.out}")

    # Null distribution of the final headline sigma on fresh simulated frames.
    rng2 = np.random.default_rng(args.seed + 1)
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 1) as ex:
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
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
