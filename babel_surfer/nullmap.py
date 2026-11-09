"""Empirical null maps: turn a raw test statistic into an honest sigma.

A test's raw statistic is a maximum over many windows, offsets or angles, so
on static it is neither centred at 0 nor spread like N(0, 1), and its upper
tail is Gumbel-like rather than Gaussian. Standardising it with a mean and
standard deviation mislabels how rare a value is. Instead we learn its
distribution on real Babelia frames and map each value to the normal
quantile with the same upper-tail probability:

* **body** (as far as the data reaches: up to the value only ``TAIL_K``
  frames exceed): the empirical CDF, stored as quantile knots and
  interpolated linearly;
* **tail** (above it): a generalised Pareto fit to the exceedances over the
  99th percentile, with the shape clamped at xi >= 0 (an exponential tail
  or heavier), which can only understate significance;
* **far beyond the data**: the raw statistics are themselves z-like, and for
  a maximum of Gaussian scores sigma grows about one-for-one with the raw
  value, so beyond the last knot sigma never rises more slowly than that
  (or than the test's own pace over its upper knots, if that is slower).
  This keeps real pictures (raw values in the hundreds or more) far above
  every threshold. (Starting that floor at the 99th percentile instead
  overstated steep tails: the colours test's 1-in-4,000 values read 4.1
  sigma instead of about 3.5.)

The resulting sigma is monotone in the raw value and, on static, distributed
as N(0, 1): a 4-sigma reading has the false-positive rate of a 4-sigma event.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

_N = NormalDist()
TAIL_P = 0.01                    # body / tail split: the top 1%
BODY_P = np.concatenate([[0.0005, 0.001, 0.002, 0.005],
                         np.linspace(0.01, 0.99, 197)])
TAIL_K = 10                      # last empirical knot: the value only this many frames exceed
UPPER_P = np.array([0.995, 0.998, 0.999, 0.9995, 0.9998, 0.9999, 0.99995, 0.99998, 0.99999])
Z_FLOOR = -8.0
OUTLIER_RAW = 1.0e5               # raw values this large are pictures, never static
Z_MAX = 6.0e4                     # keeps every score finite in the float16 score log


def _z_from_log_sf(log_sf: float) -> float:
    """Normal quantile whose upper-tail probability is exp(log_sf)."""
    if log_sf > -700:
        p = math.exp(log_sf)
        if p >= 1.0:
            return Z_FLOOR
        return -_N.inv_cdf(min(max(p, 1e-300), 1 - 1e-16))
    # asymptotic inverse of the Mills ratio: log sf ~ -z^2/2 - log(z sqrt(2 pi))
    t = -2.0 * log_sf
    z = math.sqrt(t)
    for _ in range(3):
        z = math.sqrt(max(t - 2.0 * math.log(z * math.sqrt(2 * math.pi)), 1.0))
    return z


def fit(values: np.ndarray) -> dict:
    """Fit a null map to raw statistics measured on static."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v) & (v < OUTLIER_RAW)]       # flat channels and the like aren't static
    if len(v) < 1000:
        raise ValueError(f"need at least 1000 null values, got {len(v)}")
    # Several tests are clipped at zero ("no evidence"), which puts a point
    # mass at the bottom. Frames there get the mid-point of its probability.
    # Find it as the most common value among the lowest 1% (not simply the
    # minimum: two stray values just below it once hid a 42% mass at 0 from
    # the flat test, which then read -3.3 sigma for every frame there); the
    # strays count as part of it.
    lo = float(v.min())
    low = v[v <= np.quantile(v, 0.01)]
    vals, counts = np.unique(low, return_counts=True)
    if counts.max() >= 0.01 * len(v):                     # a real point mass
        lo = float(vals[np.argmax(counts)])
    atom = float(np.mean(v <= lo + 1e-6))
    atom_info = None
    probs = BODY_P
    if atom > BODY_P[0]:
        probs = BODY_P[BODY_P > atom]
        atom_info = {"x": round(lo + 1e-6, 7), "p": round(atom, 6),
                     "z": round(_N.inv_cdf(atom / 2), 5)}
    xs = np.quantile(v, probs)
    # f16 logs have ties: keep knots strictly increasing
    keep = np.concatenate([[True], np.diff(xs) > 1e-9])
    xs, ps = xs[keep], probs[keep]
    u, tail_p = float(xs[-1]), float(1.0 - ps[-1])         # the Pareto tail starts here
    # empirical knots past the 99th percentile, as far as TAIL_K exceedances
    # support (ties: a knot at x takes the share of values above x)
    srt = np.sort(v)
    upper = [float(np.quantile(srt, p)) for p in UPPER_P if (1 - p) * len(v) >= TAIL_K]
    for x in upper:
        if x > xs[-1] + 1e-9:
            above = len(v) - np.searchsorted(srt, x, side="right")
            if above >= TAIL_K:
                xs = np.append(xs, x)
                ps = np.append(ps, 1.0 - above / len(v))
    if atom_info:
        xs = np.concatenate([[atom_info["x"]], xs])
        ps = np.concatenate([[atom], ps])
    y = np.sort(v[v > u] - u)
    # A real picture logged among the static frames would dominate a moment
    # fit of the tail; drop exceedances far beyond the rest (more than 4x the
    # 95th percentile of the exceedances) before fitting.
    if len(y) >= 20:
        y = y[y <= 4.0 * float(np.quantile(y, 0.95))]
    m = float(y.mean()) if len(y) else 1e-3
    s2 = float(y.var()) if len(y) > 1 else m * m
    # method of moments for the generalised Pareto, shape clamped at >= 0
    xi = max(0.0, 0.5 * (1.0 - m * m / s2)) if s2 > 0 else 0.0
    scale = 0.5 * m * (m * m / s2 + 1.0) if xi > 0 else m
    zk = [round(_N.inv_cdf(float(p)), 5) for p in ps]
    # The floor's pace beyond the data: this test's own sigma-per-raw over its
    # upper knots, never above one-for-one (a z-like statistic's pace).
    slope = 1.0
    if xs[-1] > u + 1e-9:
        slope = min(1.0, max(0.05, (zk[-1] - _N.inv_cdf(1.0 - tail_p)) / (float(xs[-1]) - u)))
    return {"x": [round(float(a), 5) for a in xs],
            "z": zk, "slope": round(slope, 5),
            "u": round(u, 5), "tail_p": round(tail_p, 6),
            "xi": round(xi, 5), "scale": round(max(scale, 1e-6), 6), "n": int(len(v)),
            "n_tail": int(len(y)), "atom": atom_info}


def sigma(m: dict, raw: float) -> float:
    """Map one raw statistic to sigma."""
    xs, zs = m["x"], m["z"]
    if not math.isfinite(raw):           # +inf: off the scale; NaN / -inf: no evidence
        return Z_MAX if raw == math.inf else Z_FLOOR
    a = m.get("atom")
    if a and raw <= a["x"]:
        return a["z"]
    if raw <= xs[0]:
        return max(Z_FLOOR, zs[0] + (raw - xs[0]))
    if raw <= xs[-1]:
        return float(np.interp(raw, xs, zs))
    y = raw - m["u"]
    xi, sc = m["xi"], m["scale"]
    log_sf = math.log(m["tail_p"]) + (-math.log1p(xi * y / sc) / xi if xi > 0 else -y / sc)
    return min(Z_MAX, max(_z_from_log_sf(log_sf), zs[-1] + m.get("slope", 1.0) * (raw - xs[-1])))


def sigma_array(m: dict, raw: np.ndarray) -> np.ndarray:
    """Vectorised :func:`sigma` (the tail, at most a few percent, per element)."""
    raw = np.asarray(raw, dtype=float)
    out = np.interp(raw, m["x"], m["z"])
    lo = raw < m["x"][0]
    out[lo] = np.maximum(Z_FLOOR, m["z"][0] + (raw[lo] - m["x"][0]))
    a = m.get("atom")
    if a:
        out[raw <= a["x"]] = a["z"]
    hi = np.flatnonzero(raw > m["x"][-1])
    for i in hi:
        out[i] = sigma(m, float(raw[i]))
    out[np.isnan(raw)] = np.nan          # not measured (rows logged before a test existed)
    out[raw == np.inf] = Z_MAX
    out[raw == -np.inf] = Z_FLOOR
    return np.minimum(out, Z_MAX)
