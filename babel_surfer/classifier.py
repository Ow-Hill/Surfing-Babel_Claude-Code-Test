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

CHANNELS = ("Y", "Cb", "Cr")
SHAPE_SCALES = (4, 8, 16, 32, 64)
BLOB_SCALES = (8, 12, 16, 24, 32, 48)
LINE_BANDS = (1, 2, 4, 8)
TESTS = ("shape", "blob", "lines", "texture", "palette", "tone")

#: Sigma tiers. A frame is saved as a find at ``INTERESTING_SIGMA`` or above.
#: Real static never exceeded ~4 sigma in calibration (see README), so 8 is
#: a wide safety margin while still catching shapes too faint to see easily.
INTERESTING_SIGMA = 8.0
TIERS = (            # (minimum sigma, label)
    (40.0, "striking"),
    (16.0, "clear"),
    (INTERESTING_SIGMA, "faint"),
    (5.0, "near miss"),
    (float("-inf"), "static"),
)

CALIBRATION_FILE = Path(__file__).with_name("calibration.json")


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


def _var_ratio_z(observed: float, expected: float, m: int) -> float:
    """Variance of m i.i.d. means vs. its expectation, as a z-score."""
    if expected <= 0 or m < 3:
        return 0.0
    r = observed / expected
    return (r - 1.0) / math.sqrt(2.0 / (m - 1))


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


def _window_sums(ch: np.ndarray, kh: int, kw: int, stride: int) -> np.ndarray:
    """Sums over every kh x kw window (step ``stride``) via an integral image."""
    ii = np.pad(ch, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    ys = np.arange(0, ch.shape[0] - kh + 1, stride)
    xs = np.arange(0, ch.shape[1] - kw + 1, stride)
    y0, x0 = np.ix_(ys, xs)
    return ii[y0 + kh, x0 + kw] - ii[y0, x0 + kw] - ii[y0 + kh, x0] + ii[y0, x0]


def _test_blob(ch: np.ndarray, mu: float, sigma: float) -> float:
    """Most extreme square window anywhere in the frame (sliding, so a small
    object is never split across a block boundary)."""
    best = 0.0
    for k in BLOB_SCALES:
        sums = _window_sums(ch, k, k, max(1, k // 4))
        zs = (sums / (k * k) - mu) / (sigma / k)
        best = max(best, _bonferroni_max_z(zs))
    return best


def _test_lines(ch: np.ndarray, mu: float, sigma2: float) -> float:
    """Rows / columns, and bands of 2-8 adjacent rows or columns."""
    h, w = ch.shape
    sigma = math.sqrt(sigma2)
    rows, cols = ch.mean(axis=1), ch.mean(axis=0)
    best = max(_var_ratio_z(float(rows.var()), sigma2 / w, h),
               _var_ratio_z(float(cols.var()), sigma2 / h, w))
    for band in LINE_BANDS:
        rz = _window_sums(ch, band, w, 1).ravel() / (band * w)
        cz = _window_sums(ch, h, band, 1).ravel() / (band * h)
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
    best = 0.0
    hists = tone_histogram(rgb)
    for c in range(3):
        counts = hists[c]
        frac = expected_frac[c] if expected_frac is not None else np.full(16, 1 / 16)
        exp = counts.sum() * frac
        chi2 = float(((counts - exp) ** 2 / exp).sum())
        df = 15
        best = max(best, (chi2 - df) / math.sqrt(2 * df))
    return best


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------
_calibration_cache: dict | None = None


def load_calibration(path: Path = CALIBRATION_FILE) -> dict:
    """Returns {"tests": {test: {mean, std}}, "tone_expected": array|None}."""
    global _calibration_cache
    if _calibration_cache is None:
        data = json.loads(path.read_text()) if path.exists() else {}
        tone = data.get("tone_expected")
        _calibration_cache = {
            "tests": data.get("tests") or {t: {"mean": 0.0, "std": 1.0} for t in TESTS},
            "tone_expected": np.asarray(tone) if tone is not None else None,
        }
    return _calibration_cache


def raw_scores(image: Image.Image | bytes | str | Path,
               tone_expected: np.ndarray | None = None) -> dict:
    """Uncalibrated z-score for every test (max over channels)."""
    if isinstance(image, (bytes, bytearray)):
        image = Image.open(io.BytesIO(image))
    elif not isinstance(image, Image.Image):
        image = Image.open(image)
    image = image.convert("RGB")
    rgb = np.asarray(image, dtype=np.float64)
    ycc = np.asarray(image.convert("YCbCr"), dtype=np.float64)

    out = {t: 0.0 for t in TESTS}
    flat = False
    for i in range(3):
        ch = ycc[..., i]
        mu, sigma2 = float(ch.mean()), float(ch.var())
        if sigma2 < 1e-6:
            flat = True
            continue
        out["shape"] = max(out["shape"], _test_shape(ch, sigma2))
        out["blob"] = max(out["blob"], _test_blob(ch, mu, math.sqrt(sigma2)))
        out["lines"] = max(out["lines"], _test_lines(ch, mu, sigma2))
        out["texture"] = max(out["texture"], _test_texture(ch))
    out["palette"] = _test_palette(rgb) if not flat else 1e6
    if tone_expected is None:
        tone_expected = load_calibration()["tone_expected"]
    out["tone"] = _test_tone(rgb, tone_expected)
    if flat:  # a perfectly flat channel is the opposite of static
        out["shape"] = max(out["shape"], 1e6)
    return out


def classify(image: Image.Image | bytes | str | Path,
             threshold: float = INTERESTING_SIGMA) -> Classification:
    cal = load_calibration()["tests"]
    raw = raw_scores(image)
    tests = {}
    for t, z in raw.items():
        c = cal.get(t, {"mean": 0.0, "std": 1.0})
        tests[t] = round((z - c["mean"]) / max(c["std"], 1e-9), 3)
    reason = max(tests, key=tests.get)
    sigma = tests[reason]
    return Classification(sigma=round(sigma, 3), label=tier_for(sigma),
                          is_interesting=sigma >= threshold, reason=reason,
                          tests=tests)
