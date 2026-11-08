"""Tests for what a person notices first, built to be cheap and sensitive.

The original ten tests (classifier.py) each look at one channel at a time
and take the strongest. These six look at the frame the way the eye does:
blurred to a viewing scale, with the three colour channels pooled, so a
coloured shape that is weak in every channel still adds up.

======== ===============================================================
test     what it catches
======== ===============================================================
spot     round or soft objects of any colour: Gaussian scale-space (the
         matched filter for a blob in white noise), RGB pooled as chi^2(3)
curves   arcs, outlines and strokes: elongated, oriented line filters
         (8 directions, ~30 px long, ~4 px wide), the matched filter for a
         locally straight piece of a curve; RGB pooled
flat     single-colour strokes and flat patches, from Babelia's own 16
         levels per channel: neighbouring pixels that (almost) match
colours  areas drawn with few colours (cartoons, text on a background,
         posterised areas): distinct colours per 16-64 px window
spectrum photo-like structure: excess power at low spatial frequencies
         (natural images fall off as 1/f; static is flat)
face     face-like layouts (pareidolia): two eyes over a mouth, darker
         or lighter than their surroundings, at four sizes
======== ===============================================================

Everything shares one FFT per colour channel and a handful of integral
images. Each returns a raw z-like statistic; ``nullmap`` turns it into an
honest sigma, like the other tests. ``local_maps`` gives the same evidence
per 16 px cell for the overlay.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

LUMA = np.array([0.299, 0.587, 0.114])
# (pyramid level, sigma at that level): level k is the frame averaged over
# 2^k x 2^k blocks, which keeps static white and costs 4^k times less, so
# larger features are looked for on smaller copies (sigma in full-size px =
# sigma * 2^k).
SPOT_SCALES = ((0, 3.0), (1, 3.0), (1, 6.0), (2, 6.0))      # 3, 6, 12, 24 px
FACE_SCALES = ((0, 3.0), (1, 3.0), (1, 6.0))                # eye size 3, 6, 12 px
CURVE_LEVEL, CURVE_LONG, CURVE_SHORT, CURVE_DIRECTIONS = 1, 8.0, 1.0, 8   # 16 x 2 px
FLAT_WINDOWS = (8, 16, 32, 64)
PALETTE_WINDOWS = ((16, 16), (32, 16), (64, 32))   # (size, stride)
SPECTRUM_RADII = (2, 4, 8, 16, 32)          # cycles per frame
# strokes: thin strips (width x length px) at 12 angles whose colour barely varies
STROKE_STRIPS = ((2, 16), (3, 32))
STROKE_ANGLES = tuple(range(0, 180, 15))
STROKE_VAR = 3 * 17.0 ** 2 * (16 ** 2 - 1) / 12      # static's colour variance (3 channels)
PERCEPTUAL_TESTS = ("spot", "curves", "flat", "colours", "spectrum", "face", "strokes")
LOG10E = math.log10(math.e)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _z_upper(log10_p: float) -> float:
    from .classifier import _z_from_upper_p
    return _z_from_upper_p(min(0.0, log10_p))


def _log10_sf_normal(z: float) -> float:
    from .classifier import _log10_two_sided_p
    return math.log10(0.5) + _log10_two_sided_p(z) if z > 0 else 0.0


def _bonf(top_z: float, m: float) -> float:
    """One-sided: 'the largest of m unit normals is top_z' as a z-score."""
    if top_z <= 0:
        return 0.0
    return _z_upper(_log10_sf_normal(top_z) + math.log10(max(m, 1.0)))


def _wh3(chi2: np.ndarray) -> np.ndarray:
    """chi^2 with 3 degrees of freedom -> z (Wilson-Hilferty)."""
    a = 2.0 / 27.0
    return (np.cbrt(chi2 / 3.0) - (1.0 - a)) / math.sqrt(a)


def _robust_z(x: np.ndarray, sign: int = 1) -> np.ndarray:
    """Standardise by the median and MAD over the frame: most of every frame
    is static, so this cancels JPEG and correlation effects the analytic
    null would miss."""
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * 1.4826
    return sign * (x - med) / max(mad, 1e-9)


def _integral(a: np.ndarray) -> np.ndarray:
    return np.pad(a, ((1, 0), (1, 0))).cumsum(0).cumsum(1)


def _windows(ii: np.ndarray, k: int, stride: int) -> np.ndarray:
    h, w = ii.shape[0] - 1, ii.shape[1] - 1
    ys = np.arange(0, h - k + 1, stride)[:, None]
    xs = np.arange(0, w - k + 1, stride)[None, :]
    return ii[ys + k, xs + k] - ii[ys, xs + k] - ii[ys + k, xs] + ii[ys, xs]


def levels(rgb: np.ndarray) -> np.ndarray:
    """Babelia's 16 levels per channel, recovered from the JPEG (about 82% of
    values come back exactly; the rest are off by one)."""
    return np.clip(np.rint(rgb / 17.0), 0, 15).astype(np.int16)


class Frame:
    """Shared pre-processing for one frame (computed lazily, once)."""

    def __init__(self, rgb: np.ndarray):
        self.rgb = rgb
        self.h, self.w = rgb.shape[:2]
        x = rgb.astype(np.float32)
        mu = x.mean(axis=(0, 1))
        sd = np.maximum(x.std(axis=(0, 1)), 1e-6)
        self.x = (x - mu) / sd                                   # unit-variance channels
        self.F = [np.fft.rfft2(self.x[..., c]) for c in range(3)]
        fy = np.fft.fftfreq(self.h)[:, None]
        fx = np.fft.rfftfreq(self.w)[None, :]
        self.f2 = (fy * fy + fx * fx).astype(np.float32)
        # every rfft bin counts once in the full spectrum except column 0
        # (and the Nyquist column) whose conjugate partners are also stored
        self.mult = np.full(self.f2.shape, 2.0, np.float32)
        self.mult[:, 0] = 1.0
        if self.w % 2 == 0:
            self.mult[:, -1] = 1.0
        self._lv = None
        self._blur: dict = {}
        self._down: dict = {}

    def level(self, k: int) -> "Frame":
        """This frame averaged over 2^k x 2^k blocks (cached)."""
        if k == 0:
            return self
        if k not in self._down:
            f = 2 ** k
            h, w = self.h // f * f, self.w // f * f
            small = self.rgb[:h, :w].reshape(h // f, f, w // f, f, 3).mean(axis=(1, 3))
            self._down[k] = Frame(small)
        return self._down[k]

    @property
    def lv(self):
        if self._lv is None:
            self._lv = levels(self.rgb)
        return self._lv

    def blur_z(self, sigma: float, which: str = "rgb") -> np.ndarray:
        """Gaussian-blurred channel(s) as unit-variance z fields under the
        null (white noise blurred by a known kernel). ``which``: 'rgb' ->
        (3, h, w); 'luma' -> (h, w). Luma reuses the RGB fields when they
        are already computed (it is a fixed mix of them)."""
        key = (sigma, which)
        if key in self._blur:
            return self._blur[key]
        l = (LUMA / np.linalg.norm(LUMA)).astype(np.float32)
        if which == "luma" and (sigma, "rgb") in self._blur:
            g = self._blur[(sigma, "rgb")]
            out = l[0] * g[0] + l[1] * g[1] + l[2] * g[2]
        else:
            H = self.gaussian(sigma, sigma)
            var = self.var_of(H)
            if which == "luma":
                spec = l[0] * self.F[0] + l[1] * self.F[1] + l[2] * self.F[2]
                out = np.fft.irfft2(spec * H, (self.h, self.w)) / math.sqrt(var)
            else:
                out = np.stack([np.fft.irfft2(F * H, (self.h, self.w)) for F in self.F]) / math.sqrt(var)
        self._blur[key] = out = out.astype(np.float32)
        return out

    def gaussian(self, s_along: float, s_across: float, theta: float = 0.0) -> np.ndarray:
        """Transfer function of a (possibly elongated) Gaussian at ``theta``."""
        fy = np.fft.fftfreq(self.h).astype(np.float32)[:, None]
        fx = np.fft.rfftfreq(self.w).astype(np.float32)[None, :]
        u = fx * math.cos(theta) + fy * math.sin(theta)
        v = -fx * math.sin(theta) + fy * math.cos(theta)
        return np.exp(-2.0 * math.pi ** 2 * (s_along ** 2 * u * u + s_across ** 2 * v * v))

    def var_of(self, H: np.ndarray) -> float:
        """Variance of white noise filtered by H (Parseval over all bins)."""
        return float((H * H * self.mult).sum()) / (self.h * self.w)


# --------------------------------------------------------------------------
# the tests (raw statistics)
# --------------------------------------------------------------------------
def spot_maps(fr: Frame):
    """Per scale: (pyramid level, sigma there, z map of a Gaussian spot of
    any colour, RGB pooled)."""
    for k, s in SPOT_SCALES:
        g = fr.level(k).blur_z(s)
        yield k, s, _wh3((g * g).sum(axis=0))


def test_spot(fr: Frame) -> float:
    best = 0.0
    for k, s, z in spot_maps(fr):
        resels = z.size / (4 * math.pi * s * s)
        best = max(best, _bonf(float(z.max()), resels))
    return best


def curve_field(fr: Frame) -> np.ndarray:
    """z map of the strongest oriented line segment through each pixel, any
    colour (RGB pooled as chi^2(3)), over 8 directions; at pyramid level
    CURVE_LEVEL (half size: a 2 px line keeps its contrast-to-noise there,
    at a quarter of the cost)."""
    fr = fr.level(CURVE_LEVEL)
    best = None
    for i in range(CURVE_DIRECTIONS):
        H = fr.gaussian(CURVE_LONG, CURVE_SHORT, math.pi * i / CURVE_DIRECTIONS)
        sd = math.sqrt(fr.var_of(H))
        g = np.stack([np.fft.irfft2(F * H, (fr.h, fr.w)) for F in fr.F]) / sd
        z = _wh3((g * g).sum(axis=0))
        best = z if best is None else np.maximum(best, z)
    return best


def test_curves(fr: Frame) -> float:
    z = curve_field(fr)
    m = CURVE_DIRECTIONS * z.size / (4 * math.pi * CURVE_LONG * CURVE_SHORT)
    return _bonf(float(z.max()), m)


def _poisson_z(x: np.ndarray, mu: np.ndarray) -> np.ndarray:
    """Upper-tail z of a Poisson count (Wilson-Hilferty / Byar)."""
    x = np.maximum(x, 0.5)
    return 3.0 * np.sqrt(x) * (1.0 - 1.0 / (9.0 * x) - np.cbrt(mu / x))


def flat_maps(fr: Frame):
    """Per window: z of how many neighbouring pixel pairs (almost) match:
    every channel within one of Babelia's levels."""
    lv = fr.lv
    sim_h = (np.abs(np.diff(lv, axis=1)) <= 1).all(axis=2)
    sim_v = (np.abs(np.diff(lv, axis=0)) <= 1).all(axis=2)
    e = np.zeros((fr.h, fr.w), np.float64)
    e[:, :-1] += sim_h
    e[:-1, :] += sim_v
    lam = max(float(e.mean()), 1e-6)
    ii = _integral(e)
    for k in FLAT_WINDOWS:
        stride = max(1, k // 2)
        x = _windows(ii, k, stride)
        yield k, stride, _poisson_z(x, lam * k * k)


def test_flat(fr: Frame) -> float:
    return max(_bonf(float(z.max()), z.size) for _, _, z in flat_maps(fr))


def palette_maps(fr: Frame):
    """Per window: robust z of 'fewer distinct colours than the rest of the
    frame' (4,096 possible colours from Babelia's levels)."""
    lv = fr.lv
    codes = (lv[..., 0] * 256 + lv[..., 1] * 16 + lv[..., 2]).astype(np.int64)
    for k, stride in PALETTE_WINDOWS:
        ny = (fr.h - k) // stride + 1
        nx = (fr.w - k) // stride + 1
        sy, sx = codes.strides
        win = np.lib.stride_tricks.as_strided(codes, (ny, nx, k, k), (sy * stride, sx * stride, sy, sx))
        flat = win.reshape(ny * nx, k * k)
        seen = np.zeros((ny * nx, 4096), bool)
        seen[np.arange(ny * nx)[:, None], flat] = True
        distinct = seen.sum(axis=1).reshape(ny, nx).astype(np.float64)
        yield k, stride, _robust_z(distinct, sign=-1)


def test_colours(fr: Frame) -> float:
    return max(_bonf(float(z.max()), z.size) for _, _, z in palette_maps(fr))


def test_spectrum(fr: Frame) -> float:
    """Low-frequency power, RGB pooled: sum of |F|^2/N over the lowest
    frequencies is Gamma(3K) under the null (chi^2 with 6K dof, halved)."""
    from .classifier import _wh_z
    n = fr.h * fr.w
    power = sum(np.abs(F) ** 2 for F in fr.F) / n
    ry = np.abs(np.fft.fftfreq(fr.h) * fr.h)[:, None]
    rx = (np.fft.rfftfreq(fr.w) * fr.w)[None, :]
    r = np.sqrt(ry * ry + rx * rx)
    # count each conjugate pair once: in column 0 keep only the upper half
    keep = np.ones(r.shape, bool)
    keep[fr.h // 2 + 1:, 0] = False
    keep[0, 0] = False                                         # the mean
    best = 0.0
    for R in SPECTRUM_RADII:
        sel = keep & (r <= R)
        k = int(sel.sum())
        if k == 0:
            continue
        chi2 = 2.0 * float(power[sel].sum())                   # chi^2 with 2*3*k dof
        best = max(best, _bonf(float(_wh_z(chi2, 6 * k)), len(SPECTRUM_RADII)))
    return best


def face_maps(fr: Frame):
    """Per eye size: robust z of 'two eyes over a mouth', darker or lighter
    than their surroundings (luma, blurred to the feature size)."""
    for k, s in FACE_SCALES:
        lv = fr.level(k)
        feat = lv.blur_z(s, "luma")
        surround = lv.blur_z(4.0 * s, "luma")
        # back to mean units, so feature and surround are comparable
        a = 1.0 / (2.0 * math.sqrt(math.pi) * s)
        b = 1.0 / (2.0 * math.sqrt(math.pi) * 4.0 * s)
        dy_e, dx_e, dy_m = int(round(1.2 * s)), int(round(2.0 * s)), int(round(2.0 * s))
        eyes = np.roll(feat, (dy_e, dx_e), (0, 1)) + np.roll(feat, (dy_e, -dx_e), (0, 1))
        mouth = (np.roll(feat, (-dy_m, int(s)), (0, 1)) + np.roll(feat, (-dy_m, 0), (0, 1))
                 + np.roll(feat, (-dy_m, -int(s)), (0, 1))) / 3.0
        d = a * (eyes / 2.0 + mouth) / 2.0 - b * surround
        z = _robust_z(d.astype(np.float64))
        yield k, s, np.abs(z)


def test_face(fr: Frame) -> float:
    best = 0.0
    for k, s, z in face_maps(fr):
        m = 2 * z.size / (4 * math.pi * s * s)               # two signs
        best = max(best, _bonf(float(z.max()), m))
    return best


def stroke_variance(rgb: np.ndarray):
    """For each angle, the colour variance (summed over R, G, B) of every
    strip of each STROKE_STRIPS size lying inside the frame; yields
    (angle, (width, length), variance array in rotated coordinates)."""
    h, w = rgb.shape[:2]
    x = (rgb - 127.5).astype(np.float32)        # centred: float32 sums stay exact enough
    ones = Image.fromarray(np.ones((h, w), np.float32), "F")
    for ang in STROKE_ANGLES:
        ch = [np.asarray(Image.fromarray(np.ascontiguousarray(x[..., c]), "F").rotate(
            ang, Image.NEAREST, expand=True)) for c in range(3)]
        valid = np.asarray(ones.rotate(ang, Image.NEAREST, expand=True))
        # R, G, B, their summed squares and the in-frame mask: the strip's
        # total variance is mean(sum of squares) - sum(mean^2)
        stack = np.stack(ch + [ch[0] * ch[0] + ch[1] * ch[1] + ch[2] * ch[2], valid])
        cs = np.cumsum(np.pad(stack, ((0, 0), (0, 0), (1, 0))), axis=2)
        for wid, length in STROKE_STRIPS:
            r = cs[:, :, length:] - cs[:, :, :-length]
            r = sum(r[:, i:r.shape[1] - wid + 1 + i] for i in range(wid)) / (wid * length)
            var = r[3] - (r[0] ** 2 + r[1] ** 2 + r[2] ** 2)
            var[r[4] < 1 - 1e-4] = np.inf                  # strips reaching outside the frame
            yield ang, (wid, length), var


def test_strokes(fr: Frame) -> float:
    """A thin line or outline: a strip whose colour stays nearly constant
    along its length, where static varies from pixel to pixel. Raw value:
    log(static variance / the smallest strip variance), so pen strokes,
    outlines and small drawn marks the eye follows score high."""
    best = 0.0
    for _, _, var in stroke_variance(fr.rgb):
        best = max(best, math.log(STROKE_VAR / max(float(var.min()), 1.0)))
    return best


def stroke_where(rgb: np.ndarray) -> dict:
    """The strongest stroke: its raw statistic, centre, direction (degrees,
    along (cos, sin) in image coordinates, y down) and length in px."""
    h, w = rgb.shape[:2]
    best = {"raw": -math.inf}
    for ang, (wid, length), var in stroke_variance(rgb):
        j = np.unravel_index(int(np.argmin(var)), var.shape)
        raw = math.log(STROKE_VAR / max(float(var[j]), 1.0))
        if raw > best["raw"]:
            rh, rw = var.shape[0] + wid - 1, var.shape[1] + length
            cy, cx = j[0] + wid / 2 - rh / 2, j[1] + length / 2 - rw / 2
            t = math.radians(ang)
            best = {"raw": round(raw, 4), "x": int(round(w / 2 + cx * math.cos(t) - cy * math.sin(t))),
                    "y": int(round(h / 2 + cx * math.sin(t) + cy * math.cos(t))),
                    "angle": int(ang), "length": length}
    return best


TESTS = {"spot": test_spot, "curves": test_curves, "flat": test_flat,
         "colours": test_colours, "spectrum": test_spectrum, "face": test_face,
         "strokes": test_strokes}


def raw(rgb: np.ndarray) -> dict:
    fr = Frame(rgb)
    return {name: float(fn(fr)) for name, fn in TESTS.items()}


# --------------------------------------------------------------------------
# evidence per 16 px cell (for the overlay) and where the face is
# --------------------------------------------------------------------------
def _pool_loop(z: np.ndarray, cell: int, stride: int, k: int, gh: int, gw: int) -> np.ndarray:
    """Reference implementation of :func:`_pool` (slow; used by the tests)."""
    out = np.full((gh, gw), -np.inf)
    for (iy, ix), v in np.ndenumerate(z):
        y0, x0 = iy * stride // cell, ix * stride // cell
        y1, x1 = min(gh, (iy * stride + k) // cell), min(gw, (ix * stride + k) // cell)
        if y1 > y0 and x1 > x0:
            blk = out[y0:y1, x0:x1]
            np.maximum(blk, v, out=blk)
    return out


def _pool(z: np.ndarray, cell: int, stride: int, k: int, gh: int, gw: int) -> np.ndarray:
    """Max of a window-grid map onto the cell grid (each window's value goes
    to the cells it covers). Separable: windows covering a cell form a
    contiguous range along each axis, so take the max over rows, then columns."""
    def ranges(n_win: int, n_cell: int):
        i = np.arange(n_win)
        lo, hi = i * stride // cell, np.minimum(n_cell, (i * stride + k) // cell)
        return [np.flatnonzero((lo <= c) & (c < hi)) for c in range(n_cell)]
    out = np.full((gh, gw), -np.inf)
    rows, cols = ranges(z.shape[0], gh), ranges(z.shape[1], gw)
    tmp = np.full((gh, z.shape[1]), -np.inf)
    for cy, r in enumerate(rows):
        if r.size:
            tmp[cy] = z[r[0]:r[-1] + 1].max(axis=0)
    for cx, c in enumerate(cols):
        if c.size:
            out[:, cx] = tmp[:, c[0]:c[-1] + 1].max(axis=1)
    return out


def local_maps(rgb: np.ndarray, cell: int = 16) -> dict:
    """For the overlay: per-cell evidence (z, larger = stranger) for spot,
    curves, flat and colours, plus the best face box."""
    fr = Frame(rgb)
    gh, gw = fr.h // cell, fr.w // cell
    out = {}
    def cells(z, k):
        c = cell >> k                                   # cell size at that level
        return z[: gh * c, : gw * c].reshape(gh, c, gw, c).max(axis=(1, 3))
    best = np.full((gh, gw), -np.inf)
    for k, s, z in spot_maps(fr):
        best = np.maximum(best, cells(z, k))
    out["spot"] = best
    out["curves"] = cells(curve_field(fr), CURVE_LEVEL)
    for name, gen in (("flat", flat_maps), ("colours", palette_maps)):
        best = np.full((gh, gw), -np.inf)
        for k, stride, z in gen(fr):
            best = np.maximum(best, _pool(z, cell, stride, k, gh, gw))
        out[name] = np.where(np.isfinite(best), best, 0.0)
    face = {"z": 0.0}
    for k, s, z in face_maps(fr):
        j = np.unravel_index(int(np.argmax(z)), z.shape)
        if z[j] > face["z"]:
            f = 2 ** k
            face = {"z": round(float(z[j]), 2), "x": int(j[1]) * f + f // 2,
                    "y": int(j[0]) * f + f // 2, "size": round(5 * s * f, 1)}
    out["face"] = face
    return out
