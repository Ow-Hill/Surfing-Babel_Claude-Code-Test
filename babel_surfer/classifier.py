"""Tell random static apart from images that contain *something*.

Images in the Library of Babel's slideshow are, almost always, independent
random pixels. That makes the classification problem a statistical one: we
don't need to know *what* an image shows, only whether it departs from
"every pixel is independent noise".

Three complementary signals are computed on the luminance channel:

1. **Multi-scale block variance ratio.** Average the image over blocks of
   ``k x k`` pixels. For i.i.d. noise the variance of the block means is
   ``sigma^2 / k^2`` exactly. Any shape, gradient or region larger than a
   few pixels keeps far more variance after averaging. The ratio
   ``observed / expected`` is ~1.0 for static and grows quickly for anything
   with structure, even a faint shape buried in noise.

2. **Neighbour correlation.** Correlation between each pixel and its right /
   lower neighbour. ~0 for static, typically 0.8-0.99 for real pictures.

3. **Compressibility.** zlib on a 4-bit quantised copy. Static does not
   compress; flat areas, edges and repetition do.

The final ``score`` is driven by the block-variance ratio (the most
sensitive and best calibrated signal), with the other two used as
supporting evidence and reported for display.
"""

from __future__ import annotations

import io
import zlib
from dataclasses import asdict, dataclass

import numpy as np
from PIL import Image

# Block sizes used for the multi-scale test.
SCALES = (2, 4, 8, 16, 32)

# Analysis is done on a bounded-size copy so all images are judged alike.
MAX_SIDE = 512

DEFAULT_THRESHOLD = 3.0


@dataclass
class Classification:
    score: float              # headline "structure" score; ~1 for static
    is_interesting: bool
    label: str                # "static" | "faint structure" | "structure"
    variance_ratio: float     # max block-variance ratio over scales
    best_scale: int           # block size that gave that ratio
    neighbor_corr: float      # mean of horizontal / vertical correlation
    compress_ratio: float     # compressed / raw size (1.0 = incompressible)

    def to_dict(self) -> dict:
        return asdict(self)


def _load(image: Image.Image | bytes | str) -> Image.Image:
    if isinstance(image, Image.Image):
        return image
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(image))
    return Image.open(image)


def _luma(img: Image.Image) -> np.ndarray:
    img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, MAX_SIDE / max(w, h))
    if scale < 1.0:
        # NEAREST keeps the pixel statistics intact (no smoothing that would
        # make static look correlated).
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                         Image.NEAREST)
    return np.asarray(img.convert("L"), dtype=np.float64)


def _block_variance_ratio(a: np.ndarray, k: int, sigma2: float) -> float | None:
    h, w = a.shape
    bh, bw = h // k, w // k
    if bh * bw < 16:          # too few blocks for a meaningful estimate
        return None
    blocks = a[: bh * k, : bw * k].reshape(bh, k, bw, k).mean(axis=(1, 3))
    expected = sigma2 / (k * k)
    if expected <= 0:
        return None
    return float(blocks.var() / expected)


def _neighbor_corr(a: np.ndarray) -> float:
    def corr(x: np.ndarray, y: np.ndarray) -> float:
        x = x.ravel() - x.mean()
        y = y.ravel() - y.mean()
        d = np.sqrt((x * x).sum() * (y * y).sum())
        return float((x * y).sum() / d) if d > 0 else 1.0

    return (corr(a[:, :-1], a[:, 1:]) + corr(a[:-1, :], a[1:, :])) / 2.0


def _compress_ratio(a: np.ndarray) -> float:
    q = (a.astype(np.uint8) >> 4).astype(np.uint8)
    # pack two 4-bit values per byte so "raw" is the true information size
    flat = q.ravel()
    if flat.size % 2:
        flat = np.append(flat, 0)
    packed = (flat[0::2] << 4 | flat[1::2]).astype(np.uint8).tobytes()
    return len(zlib.compress(packed, 9)) / max(1, len(packed))


def classify(image: Image.Image | bytes | str,
             threshold: float = DEFAULT_THRESHOLD) -> Classification:
    a = _luma(_load(image))
    sigma2 = float(a.var())

    if sigma2 < 1e-6:
        # A perfectly flat image is certainly not random static.
        return Classification(score=float("inf"), is_interesting=True,
                              label="structure", variance_ratio=float("inf"),
                              best_scale=0, neighbor_corr=1.0,
                              compress_ratio=_compress_ratio(a))

    best_ratio, best_k = 0.0, 0
    for k in SCALES:
        r = _block_variance_ratio(a, k, sigma2)
        if r is not None and r > best_ratio:
            best_ratio, best_k = r, k

    nc = _neighbor_corr(a)
    cr = _compress_ratio(a)

    # Headline score: block-variance ratio, nudged up by strong supporting
    # evidence from the other two signals.
    score = best_ratio * (1.0 + max(0.0, nc)) * (1.0 + max(0.0, 0.9 - cr))

    if score >= threshold * 3:
        label = "structure"
    elif score >= threshold:
        label = "faint structure"
    else:
        label = "static"

    return Classification(score=round(score, 4),
                          is_interesting=score >= threshold,
                          label=label,
                          variance_ratio=round(best_ratio, 4),
                          best_scale=best_k,
                          neighbor_corr=round(nc, 4),
                          compress_ratio=round(cr, 4))
