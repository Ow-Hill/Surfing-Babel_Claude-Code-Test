"""How visible is a frame's structure to a person, as opposed to how
statistically unlikely it is (sigma)?

Model: the eye sees the frame blurred to its viewing resolution, in a
perceptually uniform colour space (CIELAB), and judges a patch against the
flicker that static itself shows at that size. For several (blur, patch)
sizes we take the strongest patch's colour difference (Delta E) from the
frame's typical colour and divide it by what the strongest patch of pure
static reaches at the same size. The largest ratio is the frame's
**visibility**:

* about 1: nothing stands out from static's own flicker (invisible);
* 1.4 to 2: a faint patch a careful viewer might notice;
* 2 or more: likely visible at a glance.

It is a deliberately simple model (no masking by texture, no attention, and
it under-rates thin lines, which the eye follows along their length better
than any patch average captures), so it is reported next to sigma, never
instead of it. Statistically strong
finds are often invisible: the tests integrate evidence the eye cannot.
"""

from __future__ import annotations

import math

import numpy as np

# (blur sigma px, patch px) and the strongest patch's Delta E on pure static
# (mean over simulated frames; see calibrate_constants()).
SCALES = ((2.0, 8), (4.0, 16), (4.0, 32), (8.0, 64))
STATIC_MAX = {(2.0, 8): 24.2, (4.0, 16): 11.1, (4.0, 32): 6.0, (8.0, 64): 2.5}
LIKELY, FAINT = 2.0, 1.4

_M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
_WHITE = np.array([0.9505, 1.0, 1.089])


def to_lab(rgb: np.ndarray) -> np.ndarray:
    x = np.clip(rgb, 0, 255) / 255.0
    x = np.where(x > 0.04045, ((x + 0.055) / 1.055) ** 2.4, x / 12.92)
    xyz = (x @ _M.T) / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def _blur(rgb: np.ndarray, sigma: float) -> np.ndarray:
    h, w = rgb.shape[:2]
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.rfftfreq(w)[None, :]
    H = np.exp(-2 * math.pi ** 2 * sigma ** 2 * (fy * fy + fx * fx))
    return np.stack([np.fft.irfft2(np.fft.rfft2(rgb[..., c]) * H, (h, w)) for c in range(3)], -1)


def patch_contrast(rgb: np.ndarray, sigma: float, patch: int) -> np.ndarray:
    """Delta E of each patch's mean colour (after blurring) from the frame's
    median colour."""
    lab = to_lab(_blur(rgb, sigma))
    d = lab - np.median(lab.reshape(-1, 3), axis=0)
    h, w = d.shape[0] // patch * patch, d.shape[1] // patch * patch
    cm = d[:h, :w].reshape(h // patch, patch, w // patch, patch, 3).mean(axis=(1, 3))
    return np.sqrt((cm ** 2).sum(axis=-1))


def visibility(rgb: np.ndarray) -> dict:
    best = {"ratio": 0.0}
    for sigma, patch in SCALES:
        c = patch_contrast(rgb.astype(np.float64), sigma, patch)
        j = np.unravel_index(int(np.argmax(c)), c.shape)
        r = float(c[j]) / STATIC_MAX[(sigma, patch)]
        if r > best["ratio"]:
            best = {"ratio": r, "patch": patch, "x": int((j[1] + .5) * patch),
                    "y": int((j[0] + .5) * patch), "delta_e": round(float(c[j]), 1)}
    best["ratio"] = round(best["ratio"], 2)
    best["label"] = label(best["ratio"])
    return best


def label(ratio: float) -> str:
    return "likely" if ratio >= LIKELY else "faint" if ratio >= FAINT else "invisible"


def calibrate_constants(frames: int = 24, seed: int = 0) -> dict:
    """Re-measure STATIC_MAX on simulated static."""
    import io
    from PIL import Image
    from .calibrate import babel_static
    rng = np.random.default_rng(seed)
    out = {s: [] for s in SCALES}
    for _ in range(frames):
        rgb = np.asarray(Image.open(io.BytesIO(babel_static(rng))).convert("RGB"), dtype=np.float64)
        for sigma, patch in SCALES:
            out[(sigma, patch)].append(float(patch_contrast(rgb, sigma, patch).max()))
    return {k: round(float(np.mean(v)), 1) for k, v in out.items()}


if __name__ == "__main__":
    print(calibrate_constants())
