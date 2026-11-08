"""Tell Babel's random static apart from images that contain *something*.

Every Babelia frame is 640x416 pixels, each pixel an independent random
12-bit colour, delivered as a quality-90 4:4:4 JPEG. Anything a person would
call "a picture" breaks that independence somewhere, so the detector runs a
battery of statistical tests, each asking "how far is this frame from
static?", in standard deviations (sigma):

=========  ==============================================================
test       what it catches
=========  ==============================================================
shape      variance of k x k block means, k = 4..64. Static averages out to
           exactly sigma^2/k^2; shapes, regions and gradients don't.
blob       the most extreme sliding k x k window, k = 8..48 (small, strong
           objects that barely move the global variance).
lines      rows, columns and 2-8 px bands: horizons, stripes, text lines.
texture    neighbour-pixel correlation (horizontal, vertical, diagonal):
           smooth surfaces (+) and fine checkerboards (-).
palette    correlation between colour channels: greyscale or limited-palette
           images.
tone       histogram flatness per channel: images too dark, too bright or
           using few colours.
contrast   local variance in 8-32 px windows: flat patches (even ones whose
           average colour matches the frame) and unusually noisy patches.
angled     lines and bands at 15-165 degrees (rows/columns are "lines").
repetition 2-D autocorrelation at every offset: tiles, repeated motifs,
           periodic patterns.
symmetry   mirror correlation about every vertical and horizontal axis.
=========  ==============================================================

Each test is run on luma (Y) and both chroma channels (Cb, Cr), so a shape
that differs only in hue is still caught.

Raw statistics are converted to analytic z-scores, then re-centred on the
null distribution measured from simulated Babel static put through the same
JPEG encoder (``calibration.json``, produced by ``python -m
babel_surfer.calibrate`` and checked against real Babelia frames). The
headline ``sigma`` is the largest calibrated z over all tests, and frames are
graded by fixed sigma thresholds (see ``TIERS``).
"""

from __future__ import annotations

import io
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import nullmap, perceptual

CHANNELS = ("Y", "Cb", "Cr")
SHAPE_SCALES = (4, 8, 16, 32, 64)
BLOB_SCALES = (8, 12, 16, 24, 32, 48)
LINE_BANDS = (1, 2, 4, 8)
CORE_TESTS = ("shape", "blob", "lines", "texture", "palette", "tone",
              "contrast", "angled", "repetition", "symmetry")
#: tests modelled on what people notice first (perceptual.py)
PERCEPTUAL_TESTS = ("spot", "curves", "flat", "colours", "spectrum", "face", "marks")
TESTS = CORE_TESTS + PERCEPTUAL_TESTS
CONTRAST_SCALES = (8, 16, 32)
ANGLES = (15, 30, 45, 60, 75, 105, 120, 135, 150, 165)
ANGLE_BANDS = (2, 4, 8)
REPEAT_MIN_LAG = 3          # smaller lags are neighbour correlation ("texture")
REPEAT_MIN_OVERLAP = 0.25   # only offsets where >= 25% of the frame overlaps
SYMMETRY_MIN_OVERLAP = 0.5
#: Babelia's fingerprint: in every real frame, red at row y correlates with
#: green at row y+156, and green with blue (about +10 sigma per frame,
#: r ~ 0.025; 156 rows is exactly 1/8 of the frame's colour values). It is a
#: property of the archive's generator, not of any one image, so the
#: repetition test ignores offsets within 1 px of it.
BABELIA_FINGERPRINT_LAGS = ((156, 0),)

#: Sigma tiers. A frame is saved as a find at ``INTERESTING_SIGMA`` or above.
#: About 1 in 400 frames of pure static reaches 4 sigma (4,000-frame
#: simulation), so finds between 4 and 8 sigma are statistical anomalies:
#: the most unusual static, not pictures. Pure static never reached 8 sigma
#: in calibration; anything there is a real departure from randomness.
INTERESTING_SIGMA = 4.0
PICTURE_SIGMA = 8.0
TIERS = (            # (minimum sigma, label)
    (40.0, "striking"),
    (16.0, "clear"),
    (PICTURE_SIGMA, "faint"),
    (INTERESTING_SIGMA, "anomaly"),
    (3.0, "near miss"),
    (float("-inf"), "static"),
)

PACKAGE_CALIBRATION = Path(__file__).with_name("calibration.json")
CALIBRATION_FILE = PACKAGE_CALIBRATION        # see use_calibration()


def calibration_hash() -> str:
    """What scores mean: the calibration in use and the code that applies it."""
    import hashlib
    h = hashlib.sha256(CALIBRATION_FILE.read_bytes() if CALIBRATION_FILE.exists() else b"")
    h.update(Path(__file__).with_name("nullmap.py").read_bytes())
    return h.hexdigest()[:12]


def detector_id() -> str:
    """Short hash of the detector code and calibration: saved frames record
    it, so frames scored by an older detector can be re-scored."""
    import hashlib
    h = hashlib.sha256(Path(__file__).read_bytes())
    for name in ("perceptual.py", "nullmap.py"):
        h.update(Path(__file__).with_name(name).read_bytes())
    if CALIBRATION_FILE.exists():
        h.update(CALIBRATION_FILE.read_bytes())
    return h.hexdigest()[:12]


def tier_for(sigma: float) -> str:
    for lo, label in TIERS:
        if sigma >= lo:
            return label
    return "static"


@dataclass
class Classification:
    sigma: float                  # headline: max calibrated z over tests
    label: str                    # tier name
    is_interesting: bool          # sigma >= threshold
    reason: str                   # which test fired hardest
    tests: dict = field(default_factory=dict)   # test -> calibrated z
    combined: float = 0.0         # evidence summed over all tests, calibrated
    fingerprint: float = 0.0      # strength of Babelia's 156-row fingerprint (z)
    sigma_global: float = 0.0     # headline after accounting for running every test
    raw: dict = field(default_factory=dict)     # test -> raw statistic (for recalibration)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# z-score helpers
# --------------------------------------------------------------------------
def _z_from_upper_p(log10_p: float) -> float:
    """Convert an upper-tail p-value (given as log10) to a z-score."""
    if log10_p >= 0:
        return 0.0
    p = 10.0 ** log10_p
    if p > 1e-300:
        # inverse survival via Acklam-style rational approximation is overkill;
        # use the asymptotic inversion refined by Newton steps on erfc.
        z = math.sqrt(max(0.0, -2.0 * math.log(p)))
        for _ in range(30):
            sf = 0.5 * math.erfc(z / math.sqrt(2))
            pdf = math.exp(-z * z / 2) / math.sqrt(2 * math.pi)
            if pdf == 0:
                break
            step = (sf - p) / pdf
            z += step
            if abs(step) < 1e-9:
                break
        return z
    # extremely small p: asymptotic form
    ln_p = log10_p * math.log(10)
    return math.sqrt(-2 * ln_p - math.log(-2 * ln_p * 2 * math.pi) + 1e-12)


def _log10_two_sided_p(z: float) -> float:
    z = abs(z)
    if z < 37:
        return math.log10(max(math.erfc(z / math.sqrt(2)), 1e-320))
    # asymptotic: erfc(x) ~ exp(-x^2)/(x sqrt(pi))
    x = z / math.sqrt(2)
    return (-x * x - math.log(x * math.sqrt(math.pi))) / math.log(10)


def _bonferroni_max_z(zs: np.ndarray) -> float:
    """z-score for 'the most extreme of m independent normals is this big'."""
    m = zs.size
    if m == 0:
        return 0.0
    log10_p = _log10_two_sided_p(float(np.max(np.abs(zs)))) + math.log10(m)
    return _z_from_upper_p(min(0.0, log10_p))


def _bonferroni_max_z_of(top_abs: float, m: int) -> float:
    """:func:`_bonferroni_max_z` given the largest |z| and the count."""
    if m == 0:
        return 0.0
    log10_p = _log10_two_sided_p(top_abs) + math.log10(m)
    return _z_from_upper_p(min(0.0, log10_p))


_AUTOCORR_COUNT: dict = {}


def _wh_z(chi2, df):
    """Wilson-Hilferty: a chi-square(df) value as a standard normal z.

    The cube root of chi2/df is very nearly normal. The naive
    (chi2 - df) / sqrt(2 df) is right-skewed, which overstated the upper
    tail and made colour-balance (tone) and small-sample variance tests fire
    more often than their sigma claimed."""
    a = 2.0 / (9.0 * df)
    return (np.cbrt(np.asarray(chi2, dtype=np.float64) / df) - (1.0 - a)) / math.sqrt(a)


def _upper_max_z(zs) -> float:
    """z for 'the largest of m one-sided normals is this big' (Bonferroni)."""
    zs = np.asarray(zs, dtype=np.float64)
    top = float(zs.max())
    if top <= 0:
        return 0.0
    log10_p = math.log10(0.5) + _log10_two_sided_p(top) + math.log10(zs.size)
    return _z_from_upper_p(min(0.0, log10_p))


def _var_ratio_z(observed: float, expected: float, m: int) -> float:
    """Variance of m i.i.d. means vs. its expectation, as a z-score."""
    if expected <= 0 or m < 3:
        return 0.0
    return float(_wh_z((observed / expected) * (m - 1), m - 1))


def _corr(x: np.ndarray, y: np.ndarray) -> float:
    x = x.ravel() - x.mean()
    y = y.ravel() - y.mean()
    d = math.sqrt(float((x * x).sum()) * float((y * y).sum()))
    return float((x * y).sum()) / d if d > 0 else 1.0


# --------------------------------------------------------------------------
# individual tests (each returns a raw, uncalibrated z)
# --------------------------------------------------------------------------
def _test_shape(ch: np.ndarray, sigma2: float) -> float:
    h, w = ch.shape
    best = 0.0
    for k in SHAPE_SCALES:
        bh, bw = h // k, w // k
        if bh * bw < 12:
            continue
        means = ch[: bh * k, : bw * k].reshape(bh, k, bw, k).mean(axis=(1, 3))
        best = max(best, _var_ratio_z(float(means.var()), sigma2 / (k * k),
                                      bh * bw))
    return best


def _integral(ch: np.ndarray) -> np.ndarray:
    return np.pad(ch, ((1, 0), (1, 0))).cumsum(0).cumsum(1)


def _window_sums(ch: np.ndarray, kh: int, kw: int, stride: int,
                 ii: np.ndarray | None = None) -> np.ndarray:
    """Sums over every kh x kw window (step ``stride``) via an integral image
    (pass ``ii`` to reuse one already built for this channel)."""
    if ii is None:
        ii = _integral(ch)
    ys = np.arange(0, ch.shape[0] - kh + 1, stride)
    xs = np.arange(0, ch.shape[1] - kw + 1, stride)
    y0, x0 = np.ix_(ys, xs)
    return ii[y0 + kh, x0 + kw] - ii[y0, x0 + kw] - ii[y0 + kh, x0] + ii[y0, x0]


def _test_blob(ch: np.ndarray, mu: float, sigma: float, ii=None) -> float:
    """Most extreme square window anywhere in the frame (sliding, so a small
    object is never split across a block boundary)."""
    best = 0.0
    for k in BLOB_SCALES:
        sums = _window_sums(ch, k, k, max(1, k // 4), ii)
        zs = (sums / (k * k) - mu) / (sigma / k)
        best = max(best, _bonferroni_max_z(zs))
    return best


def _test_lines(ch: np.ndarray, mu: float, sigma2: float, ii=None) -> float:
    """Rows / columns, and bands of 2-8 adjacent rows or columns."""
    h, w = ch.shape
    sigma = math.sqrt(sigma2)
    rows, cols = ch.mean(axis=1), ch.mean(axis=0)
    best = max(_var_ratio_z(float(rows.var()), sigma2 / w, h),
               _var_ratio_z(float(cols.var()), sigma2 / h, w))
    for band in LINE_BANDS:
        rz = _window_sums(ch, band, w, 1, ii).ravel() / (band * w)
        cz = _window_sums(ch, h, band, 1, ii).ravel() / (band * h)
        best = max(best,
                   _bonferroni_max_z((rz - mu) / (sigma / math.sqrt(band * w))),
                   _bonferroni_max_z((cz - mu) / (sigma / math.sqrt(band * h))))
    return best


def _test_texture(ch: np.ndarray) -> float:
    pairs = ((ch[:, :-1], ch[:, 1:]), (ch[:-1, :], ch[1:, :]),
             (ch[:-1, :-1], ch[1:, 1:]), (ch[:-1, 1:], ch[1:, :-1]))
    return max(abs(_corr(a, b)) * math.sqrt(a.size) for a, b in pairs)


def _test_palette(rgb: np.ndarray) -> float:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    n = r.size
    return max(abs(_corr(r, g)), abs(_corr(g, b)), abs(_corr(r, b))) * math.sqrt(n)


def tone_histogram(rgb: np.ndarray) -> np.ndarray:
    """16-bin histogram per RGB channel, shape (3, 16)."""
    return np.stack([np.histogram(rgb[..., c], bins=16, range=(0, 256))[0]
                     for c in range(3)])


def _test_tone(rgb: np.ndarray, expected_frac: np.ndarray | None) -> float:
    """Chi-square of a 16-bin histogram vs. what static looks like, per RGB
    channel. ``expected_frac`` is the null histogram from calibration (JPEG
    clipping makes static's histogram slightly non-flat at 0 and 255)."""
    hists = tone_histogram(rgb)
    zs = []
    for c in range(3):
        counts = hists[c]
        frac = expected_frac[c] if expected_frac is not None else np.full(16, 1 / 16)
        exp = counts.sum() * frac
        chi2 = float(((counts - exp) ** 2 / exp).sum())
        zs.append(float(_wh_z(chi2, 15)))
    # only too-uneven histograms count (one-sided), worst of three channels
    return _upper_max_z(zs)


def _test_contrast(ch: np.ndarray, sigma2: float, ii=None, ii2=None) -> float:
    """Most extreme local variance (flat or noisy patch) vs. static."""
    best = 0.0
    for k in CONTRAST_SCALES:
        n = k * k
        s1 = _window_sums(ch, k, k, max(1, k // 2), ii)
        s2 = _window_sums(ch * ch, k, k, max(1, k // 2), ii2)
        var = np.maximum((s2 - s1 * s1 / n) / (n - 1), 0.0)
        zs = _wh_z(var / sigma2 * (n - 1), n - 1)
        best = max(best, _bonferroni_max_z(zs))
    return best


_ROTATION_CACHE: dict = {}


def _rotation_index(shape: tuple[int, int], angle: float):
    """Where each pixel lands when the frame is rotated by ``angle`` (PIL
    nearest neighbour, expand=True), computed once per frame size and angle:
    (source pixel index, output row) for every covered output pixel, and the
    pixel count of each output row. Rotating an image of pixel indices gives
    exactly the mapping PIL uses, so results match rotating the frame itself."""
    key = (shape, angle)
    hit = _ROTATION_CACHE.get(key)
    if hit is None:
        h, w = shape
        idx = np.arange(1, h * w + 1, dtype=np.float32).reshape(h, w)   # 0 = outside
        rot = np.asarray(Image.fromarray(idx, "F").rotate(angle, resample=Image.NEAREST,
                                                          expand=True))
        rows = np.broadcast_to(np.arange(rot.shape[0])[:, None], rot.shape)
        valid = rot > 0
        src = rot[valid].astype(np.int64) - 1
        row = rows[valid].astype(np.int64)
        cnt = np.bincount(row, minlength=rot.shape[0]).astype(np.float64)
        hit = _ROTATION_CACHE[key] = (src, row, cnt)
    return hit


_ALL_ANGLES_CACHE: dict = {}


def _all_angles_index(shape: tuple[int, int]):
    """Every angle's rotation index joined end to end, so one gather and one
    bincount give the row sums of all rotated frames at once."""
    hit = _ALL_ANGLES_CACHE.get(shape)
    if hit is None:
        srcs, rows, counts, offset = [], [], [], 0
        for a in ANGLES:
            src, row, cnt = _rotation_index(shape, a)
            srcs.append(src)
            rows.append(row + offset)
            counts.append((offset, cnt))
            offset += cnt.size
        hit = _ALL_ANGLES_CACHE[shape] = (np.concatenate(srcs), np.concatenate(rows),
                                          counts, offset)
    return hit


def _band_profiles(rows: np.ndarray, cnt: np.ndarray, sigma: float) -> dict:
    ii, ic = np.concatenate([[0], np.cumsum(rows)]), np.concatenate([[0], np.cumsum(cnt)])
    out = {}
    for band in ANGLE_BANDS:
        s, c = ii[band:] - ii[:-band], ic[band:] - ic[:-band]
        ok = c >= 200 * band
        z = np.zeros_like(s)
        z[ok] = s[ok] / (sigma * np.sqrt(c[ok]))
        out[band] = (z, ok)
    return out


def angled_profiles(ch: np.ndarray, sigma: float, angle: float) -> dict:
    """For the frame rotated by ``angle``: {band: (z of every band of rows,
    usable mask)}. Bands with too few pixels are ignored."""
    src, row, cnt = _rotation_index(ch.shape, angle)
    flat = (ch - ch.mean()).ravel()
    return _band_profiles(np.bincount(row, weights=flat[src], minlength=cnt.size), cnt, sigma)


def _test_angled(ch: np.ndarray, sigma: float) -> float:
    src, row, counts, total = _all_angles_index(ch.shape)
    flat = (ch - ch.mean()).ravel()
    sums = np.bincount(row, weights=flat[src], minlength=total)
    best = 0.0
    for offset, cnt in counts:
        for z, ok in _band_profiles(sums[offset:offset + cnt.size], cnt, sigma).values():
            best = max(best, _bonferroni_max_z(z[ok]))
    return best


def autocorr_z(ch: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """z-score of the correlation between the frame and itself shifted by
    every (dy, dx), via FFT; and a mask of offsets that count (overlap large
    enough, not a tiny neighbour lag). Index [H-1+dy, W-1+dx]."""
    h, w = ch.shape
    # float32 FFT: ~1e-4 error on z, measurably faster on this large transform
    x = ((ch - ch.mean()) / max(ch.std(), 1e-9)).astype(np.float32)
    fh, fw = 2 * h, 2 * w
    f = np.fft.rfft2(x, (fh, fw))
    ac = np.fft.irfft2(f * np.conj(f), (fh, fw))
    ac = np.fft.fftshift(ac)[1:, 1:]                      # lags -(h-1)..(h-1)
    inv_sqrt_overlap, use = _autocorr_consts(h, w)
    return ac * inv_sqrt_overlap, use                      # r * sqrt(n)


_AUTOCORR_CACHE: dict = {}


def _autocorr_consts(h: int, w: int):
    """Per-lag 1/sqrt(overlap) and the mask of lags that count. They depend
    only on the frame size, so they are built once."""
    hit = _AUTOCORR_CACHE.get((h, w))
    if hit is None:
        oy = h - np.abs(np.arange(-(h - 1), h))
        ox = w - np.abs(np.arange(-(w - 1), w))
        overlap = np.outer(oy, ox).astype(np.float64)
        dy = np.abs(np.arange(-(h - 1), h))[:, None]
        dx = np.abs(np.arange(-(w - 1), w))[None, :]
        use = (overlap >= REPEAT_MIN_OVERLAP * h * w) & ((dy >= REPEAT_MIN_LAG) | (dx >= REPEAT_MIN_LAG))
        use[: h - 1, :] = False                           # each lag pair once (dy >= 0)
        use[h - 1, : w - 1] = False
        for fy, fx in BABELIA_FINGERPRINT_LAGS:
            if fy < h and abs(fx) < w:
                use[h - 2 + fy: h + 1 + fy, w - 2 + fx: w + 1 + fx] = False
        hit = _AUTOCORR_CACHE[(h, w)] = (1.0 / np.sqrt(overlap), use)
    return hit


def _test_repetition(ch: np.ndarray) -> float:
    z, use = autocorr_z(ch)
    # the max |z| over the used lags, without materialising z[use]
    return _bonferroni_max_z_of(float(np.abs(z, out=z).max(where=use, initial=0.0)),
                                int(_AUTOCORR_COUNT.setdefault(use.shape, int(use.sum()))))


def mirror_z(ch: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray]:
    """z of mirror correlation for every axis position along ``axis``
    (1 = vertical mirror line, i.e. left/right flip). Returns (z, positions
    in pixels of the mirror line)."""
    x = (ch - ch.mean()) / max(ch.std(), 1e-9)
    if axis == 0:
        x = x.T
    h, w = x.shape
    f = np.fft.rfft(x, 2 * w, axis=1)
    g = np.fft.rfft(x[:, ::-1], 2 * w, axis=1)
    # the inverse FFT is linear: sum the spectra over rows, invert once
    cc = np.fft.irfft((f * np.conj(g)).sum(axis=0), 2 * w)
    # cc[s] pairs x[c] with x[w-1-c+s]... mirror line at (w - 1 + s) / 2
    shifts = np.concatenate([np.arange(0, w), np.arange(-w, 0)])
    pairs = h * (w - np.abs(shifts))
    pos = (w - 1 + shifts) / 2.0
    ok = (w - np.abs(shifts)) >= SYMMETRY_MIN_OVERLAP * w
    z = np.zeros_like(cc)
    # every pair is counted twice in cc; independent pairs = pairs / 2
    z[ok] = (cc[ok] / pairs[ok]) * np.sqrt(pairs[ok] / 2.0)
    return z[ok], pos[ok]


def _test_symmetry(ch: np.ndarray) -> float:
    return max(_bonferroni_max_z(mirror_z(ch, 1)[0]), _bonferroni_max_z(mirror_z(ch, 0)[0]))


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------
_calibration_cache: dict | None = None


def package_calibration_hash() -> str:
    import hashlib
    return hashlib.sha256(PACKAGE_CALIBRATION.read_bytes()).hexdigest()[:16]


def live_calibration_path(data_dir) -> Path | None:
    """``<data_dir>/calibration.json``, the scan workflow's refit of the
    package calibration from real frames, if it was built on the current
    package calibration (a code release with a new calibration replaces it)."""
    p = Path(data_dir) / "calibration.json"
    try:
        if json.loads(p.read_text()).get("base") == package_calibration_hash():
            return p
    except (OSError, ValueError):
        pass
    return None


def use_calibration(path) -> None:
    """Score with this calibration file from now on (this process)."""
    global CALIBRATION_FILE, _calibration_cache
    CALIBRATION_FILE = Path(path)
    _calibration_cache = None


def activate_live_calibration(data_dir) -> Path | None:
    p = live_calibration_path(data_dir)
    use_calibration(p or PACKAGE_CALIBRATION)
    return p


def load_calibration(path: Path | None = None) -> dict:
    """Returns {"tests": {test: {mean, std}}, "tone_expected": array|None}."""
    global _calibration_cache
    if _calibration_cache is None:
        path = path or CALIBRATION_FILE
        data = json.loads(path.read_text()) if path.exists() else {}
        tone = data.get("tone_expected")
        _calibration_cache = {
            "tests": data.get("tests") or {t: {"mean": 0.0, "std": 1.0} for t in TESTS},
            # empirical null maps learned on real frames (see nullmap.py); when
            # present they replace the mean/std standardisation
            "maps": data.get("maps") or {},
            "combined_tests": tuple(data.get("combined_tests") or CORE_TESTS),
            "tone_expected": np.asarray(tone) if tone is not None else None,
        }
    return _calibration_cache


FINGERPRINT_ROWS = 156


def fingerprint_z(rgb: np.ndarray) -> float:
    """Strength of Babelia's fingerprint: red at row y vs. green at y+156,
    and green vs. blue, combined (about +14 in every real frame so far; ~0 in
    simulated static). A frame without it would be a discovery in itself."""
    def z(a, b):
        a = (a - a.mean()) / max(a.std(), 1e-9)
        b = (b - b.mean()) / max(b.std(), 1e-9)
        p, q = a[:-FINGERPRINT_ROWS], b[FINGERPRINT_ROWS:]
        return float((p * q).sum() / math.sqrt(p.size))
    if rgb.shape[0] <= FINGERPRINT_ROWS:
        return 0.0
    return (z(rgb[..., 0], rgb[..., 1]) + z(rgb[..., 1], rgb[..., 2])) / math.sqrt(2)


def raw_scores(image: Image.Image | bytes | str | Path,
               tone_expected: np.ndarray | None = None, extras: dict | None = None) -> dict:
    """Uncalibrated z-score for every test (max over channels)."""
    if isinstance(image, (bytes, bytearray)):
        image = Image.open(io.BytesIO(image))
    elif not isinstance(image, Image.Image):
        image = Image.open(image)
    image = image.convert("RGB")
    rgb = np.asarray(image, dtype=np.float64)
    ycc = np.asarray(image.convert("YCbCr"), dtype=np.float64)

    out = {t: 0.0 for t in CORE_TESTS}
    flat = False
    for i in range(3):
        ch = ycc[..., i]
        mu, sigma2 = float(ch.mean()), float(ch.var())
        if sigma2 < 1e-6:
            flat = True
            continue
        out["shape"] = max(out["shape"], _test_shape(ch, sigma2))
        ii, ii2 = _integral(ch), _integral(ch * ch)      # shared by the window tests
        out["blob"] = max(out["blob"], _test_blob(ch, mu, math.sqrt(sigma2), ii))
        out["lines"] = max(out["lines"], _test_lines(ch, mu, sigma2, ii))
        out["texture"] = max(out["texture"], _test_texture(ch))
        out["contrast"] = max(out["contrast"], _test_contrast(ch, sigma2, ii, ii2))
        out["angled"] = max(out["angled"], _test_angled(ch, math.sqrt(sigma2)))
        out["repetition"] = max(out["repetition"], _test_repetition(ch))
        out["symmetry"] = max(out["symmetry"], _test_symmetry(ch))
    out["palette"] = _test_palette(rgb) if not flat else 1e6
    if tone_expected is None:
        tone_expected = load_calibration()["tone_expected"]
    out["tone"] = _test_tone(rgb, tone_expected)
    if flat:  # a perfectly flat channel is the opposite of static
        out["shape"] = max(out["shape"], 1e6)
        out.update({t: 1e6 if t == "flat" else 0.0 for t in PERCEPTUAL_TESTS})
    else:
        out.update(perceptual.raw(rgb))
    if extras is not None:
        extras["fingerprint"] = fingerprint_z(rgb)
    return out


def combined_raw(tests: dict, names=CORE_TESTS) -> float:
    """Stouffer-style sum of the per-test calibrated z (before its own
    calibration): catches weak structure spread across several tests.
    ``names``: the tests it pools (calibration.json's ``combined_tests``;
    the original ten until the newer tests are calibrated on real frames)."""
    zs = [tests[t] for t in names if t in tests and math.isfinite(tests[t])]
    return sum(zs) / math.sqrt(len(zs)) if zs else 0.0


def classify(image: Image.Image | bytes | str | Path,
             threshold: float = INTERESTING_SIGMA) -> Classification:
    extras: dict = {}
    raw = raw_scores(image, extras=extras)
    tests = calibrate_scores(raw)
    reason = max(tests, key=tests.get)
    sigma = tests[reason]
    combined = calibrate_combined(tests)
    return Classification(sigma=round(sigma, 3), label=tier_for(sigma),
                          is_interesting=sigma >= threshold, reason=reason,
                          tests=tests, combined=round(combined, 3),
                          fingerprint=round(extras.get("fingerprint", 0.0), 2),
                          sigma_global=round(frame_sigma(sigma, len(tests)), 3),
                          raw={t: round(float(v), 5) for t, v in raw.items()})


def frame_sigma(top: float, n_tests: int) -> float:
    """The strongest of ``n_tests`` per-test sigmas as a whole-frame sigma:
    the chance that *any* test reaches ``top`` on static (Sidak, treating
    the tests as independent, which is slightly conservative)."""
    if top <= 0:
        return top
    log_p = math.log(0.5) + _log10_two_sided_p(top) * math.log(10)   # one-sided
    p = math.exp(log_p)
    log_frame = math.log(-math.expm1(n_tests * math.log1p(-p))) if p > 1e-15 else log_p + math.log(n_tests)
    return _z_from_upper_p(min(0.0, log_frame / math.log(10)))


def calibrate_scores(raw: dict) -> dict:
    """Raw test statistics -> calibrated sigma per test."""
    cal = load_calibration()
    out = {}
    for t, v in raw.items():
        m = cal["maps"].get(t)
        if m:
            out[t] = round(nullmap.sigma(m, float(v)), 3)
        else:
            c = cal["tests"].get(t, {"mean": 0.0, "std": 1.0})
            out[t] = round(min(nullmap.Z_MAX, (v - c["mean"]) / max(c["std"], 1e-9)), 3)
    return out


def calibrate_combined(tests: dict) -> float:
    cal = load_calibration()
    names = cal["combined_tests"]
    m = cal["maps"].get("combined")
    if m:
        return nullmap.sigma(m, combined_raw(tests, names))
    cc = cal["tests"].get("combined", {"mean": 0.0, "std": 1.0})
    return (combined_raw(tests, names) - cc["mean"]) / max(cc["std"], 1e-9)


# --------------------------------------------------------------------------
# helpers shared with analysis.py
# --------------------------------------------------------------------------
def _box_sum_same(a: np.ndarray, s: int) -> tuple[np.ndarray, np.ndarray]:
    """Sum over the s x s neighbourhood around each cell, clipped at the
    edges, and how many cells each sum covered."""
    h, w = a.shape
    ii = np.pad(a, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    lo = s // 2
    y0 = np.clip(np.arange(h) - lo, 0, h); y1 = np.clip(np.arange(h) - lo + s, 0, h)
    x0 = np.clip(np.arange(w) - lo, 0, w); x1 = np.clip(np.arange(w) - lo + s, 0, w)
    Y0, X0 = np.meshgrid(y0, x0, indexing="ij"); Y1, X1 = np.meshgrid(y1, x1, indexing="ij")
    total = ii[Y1, X1] - ii[Y0, X1] - ii[Y1, X0] + ii[Y0, X0]
    return total, (Y1 - Y0) * (X1 - X0)
