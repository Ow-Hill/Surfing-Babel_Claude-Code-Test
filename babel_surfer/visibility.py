"""How visible is a frame's structure to a person, as opposed to how
statistically unlikely it is (sigma)?

The eye sees Babel static as a fine, busy, colourful flicker, and notices a
region that breaks it. The model measures three ways of breaking it, each in
the perceptually uniform CIELAB colour space and each divided by what pure
static itself reaches at the same size (its strongest patch, averaged over
simulated frames), so static scores about 1 on every cue:

* **colour**: a patch whose average colour, blurred to viewing scale,
  differs from the frame's typical colour (a red disc);
* **texture**: a patch that is smoother or busier than static (a grey
  square, greyscale or dark static). The change in fine detail is judged
  on a log scale, never against less than a 10% change, the eye's rough
  limit for telling two contrasts apart;
* **line**: a thin strip (2-4 px wide, 24-64 px long, any of 12 angles)
  whose colour stays nearly constant along its length. Static varies from
  pixel to pixel, so even a faint line stands out; averaging a patch's
  colour misses it, because a 2 px line barely moves a patch's mean.

The largest ratio is the frame's **visibility**:

* about 1: nothing stands out from static (invisible);
* 1.4 to 2: something a careful viewer might notice;
* 2 or more: likely visible at a glance.

It is still a simple model (no attention, no grouping of separate marks into
a shape), so it is reported next to sigma, never instead of it. Statistically
strong finds are often invisible: the tests integrate evidence the eye cannot.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

# colour: (blur sigma px, patch px) -> the strongest patch's Delta E on static
SCALES = ((2.0, 8), (4.0, 16), (4.0, 32), (8.0, 64))
STATIC_MAX = {(2.0, 8): 24.2, (4.0, 16): 11.1, (4.0, 32): 6.0, (8.0, 64): 2.5}
# texture: patch px (0 = the whole frame) -> static's largest |log(detail / typical)|
TEXTURE_SIZES = (8, 16, 32, 64, 0)
TEXTURE_DETAIL = 60.7             # static's mean fine-detail energy (Delta E)
TEXTURE_MAX = {8: 0.238, 16: 0.108, 32: 0.048, 64: 0.021, 0: 0.001}
WEBER = 0.1                       # a smaller change in contrast is not seen
# line: (width px, length px) -> static's largest log(colour variance / typical)
STRIPS = ((2, 24), (2, 64), (4, 32))
ANGLES = tuple(range(0, 180, 15))
STATIC_VAR = 4737.0               # static's colour variance (Delta E^2, summed over L, a, b)
LINE_MAX = {(2, 24): 0.695, (2, 64): 0.384, (4, 32): 0.393}
LIKELY, FAINT = 2.0, 1.4
RATIO_CAP = 99.0                  # beyond this "how visible" means nothing more

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


def _box(a: np.ndarray, ph: int, pw: int) -> np.ndarray:
    """Mean over every ph x pw window (valid positions only)."""
    c = np.cumsum(np.cumsum(np.pad(a, ((1, 0), (1, 0))), 0), 1)
    return (c[ph:, pw:] - c[:-ph, pw:] - c[ph:, :-pw] + c[:-ph, :-pw]) / (ph * pw)


def patch_contrast(rgb: np.ndarray, sigma: float, patch: int) -> np.ndarray:
    """Delta E of each patch's mean colour (after blurring) from the frame's
    median colour."""
    lab = to_lab(_blur(rgb, sigma))
    d = lab - np.median(lab.reshape(-1, 3), axis=0)
    h, w = d.shape[0] // patch * patch, d.shape[1] // patch * patch
    cm = d[:h, :w].reshape(h // patch, patch, w // patch, patch, 3).mean(axis=(1, 3))
    return np.sqrt((cm ** 2).sum(axis=-1))


def detail(rgb: np.ndarray) -> np.ndarray:
    """Fine-detail energy per pixel: Delta E from a slightly blurred copy."""
    return np.sqrt(((to_lab(rgb) - to_lab(_blur(rgb, 1.5))) ** 2).sum(axis=-1))


def texture_change(act: np.ndarray, patch: int):
    """|log(detail / static's)| for overlapping patches (stride patch/4), or
    for the whole frame when patch is 0. Returns (values, stride)."""
    if patch == 0:
        return np.array([[abs(math.log(max(float(act.mean()), 1e-3) / TEXTURE_DETAIL))]]), 0
    s = patch // 4
    m = _box(act, patch, patch)[::s, ::s]
    return np.abs(np.log(np.maximum(m, 1e-3) / TEXTURE_DETAIL)), s


def _rotate(a: np.ndarray, ang: int) -> np.ndarray:
    return np.asarray(Image.fromarray(a.astype(np.float32), "F").rotate(
        ang, Image.NEAREST, expand=True), dtype=np.float64)


def line_scores(rgb: np.ndarray):
    """For each strip size, the strongest log(static variance / strip
    variance) over all positions and angles, and where it is in the frame:
    (score, x, y, angle), the strip running along (cos, sin) of the angle in
    image coordinates (y down)."""
    lab = to_lab(rgb)
    h, w = lab.shape[:2]
    best = {s: (-math.inf, 0, 0, 0) for s in STRIPS}
    for ang in ANGLES:
        ch = [_rotate(lab[..., c], ang) for c in range(3)]
        valid = _rotate(np.ones((h, w)), ang)
        for wid, length in STRIPS:
            var = sum(_box(c * c, wid, length) - _box(c, wid, length) ** 2 for c in ch)
            var = np.where(_box(valid, wid, length) > 0.999, var, np.inf)   # inside the frame only
            j = np.unravel_index(int(np.argmin(var)), var.shape)
            score = math.log(STATIC_VAR / max(float(var[j]), 1.0))
            if score > best[(wid, length)][0]:
                # strip centre in rotated coordinates, mapped back to the frame
                rh, rw = valid.shape
                cy, cx = j[0] + wid / 2 - rh / 2, j[1] + length / 2 - rw / 2
                t = math.radians(ang)
                x = w / 2 + cx * math.cos(t) - cy * math.sin(t)
                y = h / 2 + cx * math.sin(t) + cy * math.cos(t)
                best[(wid, length)] = (score, int(round(x)), int(round(y)), ang)
    return best


def visibility(rgb: np.ndarray) -> dict:
    rgb = rgb.astype(np.float64)
    cand = []                      # (ratio, cue, extra)
    for sigma, patch in SCALES:
        c = patch_contrast(rgb, sigma, patch)
        j = np.unravel_index(int(np.argmax(c)), c.shape)
        cand.append((float(c[j]) / STATIC_MAX[(sigma, patch)], "colour",
                     {"patch": patch, "x": int((j[1] + .5) * patch), "y": int((j[0] + .5) * patch),
                      "delta_e": round(float(c[j]), 1)}))
    act = detail(rgb)
    for patch in TEXTURE_SIZES:
        v, s = texture_change(act, patch)
        j = np.unravel_index(int(np.argmax(v)), v.shape)
        where = ({"patch": patch, "x": int(j[1] * s + patch / 2), "y": int(j[0] * s + patch / 2)}
                 if patch else {"patch": 0, "x": rgb.shape[1] // 2, "y": rgb.shape[0] // 2})
        smoother = (act.mean() if not patch else _box(act, patch, patch)[::s, ::s][j]) < TEXTURE_DETAIL
        cand.append((float(v[j]) / max(TEXTURE_MAX[patch], WEBER), "texture",
                     {**where, "smoother": bool(smoother)}))
    for (wid, length), (score, x, y, ang) in line_scores(rgb).items():
        cand.append((score / LINE_MAX[(wid, length)], "line",
                     {"patch": length, "x": x, "y": y, "angle": ang}))
    parts = {}
    for r, cue, _ in cand:
        parts[cue] = max(parts.get(cue, 0.0), r)
    r, cue, extra = max(cand, key=lambda t: t[0])
    best = {"ratio": round(min(r, RATIO_CAP), 2), "cue": cue, **extra,
            "parts": {k: round(min(v, RATIO_CAP), 2) for k, v in parts.items()}}
    best["label"] = label(best["ratio"])
    return best


def label(ratio: float) -> str:
    return "likely" if ratio >= LIKELY else "faint" if ratio >= FAINT else "invisible"


def calibrate_constants(frames: int = 24, seed: int = 0) -> dict:
    """Re-measure the static constants (STATIC_MAX, TEXTURE_DETAIL,
    TEXTURE_MAX, STATIC_VAR, LINE_MAX) on simulated static. The texture and
    line maxima depend on TEXTURE_DETAIL and STATIC_VAR: update those first
    and run again."""
    import io
    from .calibrate import babel_static
    rng = np.random.default_rng(seed)
    out: dict = {}
    for _ in range(frames):
        rgb = np.asarray(Image.open(io.BytesIO(babel_static(rng))).convert("RGB"), dtype=np.float64)
        for sigma, patch in SCALES:
            out.setdefault(("colour", sigma, patch), []).append(float(patch_contrast(rgb, sigma, patch).max()))
        act = detail(rgb)
        out.setdefault("detail", []).append(float(act.mean()))
        for patch in TEXTURE_SIZES:
            out.setdefault(("texture", patch), []).append(float(texture_change(act, patch)[0].max()))
        lab = to_lab(rgb)
        out.setdefault("var", []).append(float(lab.reshape(-1, 3).var(axis=0).sum()))
        for s, (score, *_rest) in line_scores(rgb).items():
            out.setdefault(("line", *s), []).append(score)
    return {k: round(float(np.mean(v)), 3) for k, v in out.items()}


if __name__ == "__main__":
    print(calibrate_constants())
