"""Where and what: evidence maps and a plain-English description per frame.

The classifier answers "how unlikely is this frame as static?". For frames
we keep (finds, near misses, demos) this module also answers *where* and
*what*, from the full-resolution frame:

* ``maps``: the evidence behind each test, compact enough to ship as JSON
  so the website can draw it as an overlay with an adjustable threshold:
  per-cell brightness/colour departures and local contrast, row/column
  profiles, the strongest angled line, repeat offset and mirror axis, the
  colour-histogram deviations and channel/neighbour correlations.
* ``describe()``: one or two sentences generated from those maps.
* ``tone_mask_png()``: for colour-balance anomalies, a mask of the pixels in
  the most over/under-represented colour range, so even a "global" anomaly
  has something to see.
"""

from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image

from . import classifier as C

CELL = 16                       # px per map cell (640x416 -> 40x26)
ROW_BAND = 4                    # px per row/column profile entry
CHANNEL_WORDS = {               # (positive, negative) departure per channel
    "Y": ("brighter", "darker"),
    "Cb": ("bluer", "yellower"),
    "Cr": ("redder", "greener"),
}
TONE_RANGES = ("very dark", "very dark", "dark", "dark", "dark", "darkish", "mid", "mid",
               "mid", "mid", "lightish", "light", "light", "light", "very light", "very light")
RGB = ("red", "green", "blue")

# Default overlay thresholds (in sigma) per layer: about where a single cell
# / bin / row of pure static rarely goes. The site lowers them per find when
# needed so the find's strongest evidence is always visible.
LAYER_DEFAULTS = {"mean": 3.0, "contrast": 3.0, "rows": 3.0, "tone": 3.0}


def _load(image) -> Image.Image:
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(image))
    if isinstance(image, Image.Image):
        return image
    return Image.open(image)


def _cells(ch: np.ndarray, k: int = CELL):
    h, w = ch.shape
    bh, bw = h // k, w // k
    return ch[: bh * k, : bw * k].reshape(bh, k, bw, k), bh, bw


def _multiscale(z: np.ndarray) -> np.ndarray:
    """Signed z of the most extreme 1x1/2x2/4x4-cell neighbourhood (sum of n
    unit normals / sqrt(n) is again a unit normal)."""
    best = z.copy()
    for s in (2, 4):
        total, n = C._box_sum_same(z, s)
        cand = total / np.sqrt(n)
        best = np.where(np.abs(cand) > np.abs(best), cand, best)
    return best


def analyze(image) -> dict:
    img = _load(image).convert("RGB")
    rgb = np.asarray(img, dtype=np.float64)
    ycc = np.asarray(img.convert("YCbCr"), dtype=np.float64)
    h, w = ycc.shape[:2]

    mean_best = mean_ch = contrast_best = None
    rows_best = np.zeros(h // ROW_BAND)
    cols_best = np.zeros(w // ROW_BAND)
    angled = {"z": 0.0}
    repeat = {"z": 0.0}
    mirror = {"z": 0.0}
    for i, name in enumerate(C.CHANNELS):
        ch = ycc[..., i]
        mu, sd = ch.mean(), ch.std()
        if sd < 1e-6:
            continue
        blocks, bh, bw = _cells(ch)
        # brightness / colour departures per cell (signed)
        zm = _multiscale((blocks.mean(axis=(1, 3)) - mu) / (sd / CELL))
        # local contrast per cell (signed: + noisier, - flatter)
        n = CELL * CELL
        zc = _multiscale((blocks.var(axis=(1, 3), ddof=1) / sd ** 2 - 1) / math.sqrt(2 / (n - 1)))
        if mean_best is None:
            mean_best, mean_ch = zm, np.full(zm.shape, i)
            contrast_best = zc
        else:
            take = np.abs(zm) > np.abs(mean_best)
            mean_best = np.where(take, zm, mean_best)
            mean_ch = np.where(take, i, mean_ch)
            contrast_best = np.where(np.abs(zc) > np.abs(contrast_best), zc, contrast_best)
        # row / column profiles
        r = ch[: (h // ROW_BAND) * ROW_BAND].reshape(-1, ROW_BAND, w).mean(axis=(1, 2))
        c = ch[:, : (w // ROW_BAND) * ROW_BAND].reshape(h, -1, ROW_BAND).mean(axis=(0, 2))
        rz = (r - mu) / (sd / math.sqrt(ROW_BAND * w))
        cz = (c - mu) / (sd / math.sqrt(ROW_BAND * h))
        rows_best = np.where(np.abs(rz) > np.abs(rows_best), rz, rows_best)
        cols_best = np.where(np.abs(cz) > np.abs(cols_best), cz, cols_best)
        # strongest angled band
        for a in C.ANGLES:
            for band, (z, ok) in C.angled_profiles(ch, sd, a).items():
                if ok.any():
                    j = int(np.argmax(np.where(ok, np.abs(z), -1)))
                    if abs(z[j]) > abs(angled["z"]):
                        angled = {"z": float(z[j]), "angle": a, "row": j + band / 2, "band": band,
                                  "channel": name}
        # repetition
        zr, use = C.autocorr_z(ch)
        masked = np.where(use, np.abs(zr), -1)
        j = np.unravel_index(int(np.argmax(masked)), masked.shape)
        if masked[j] > abs(repeat["z"]):
            repeat = {"z": float(zr[j]), "dy": int(j[0] - (h - 1)), "dx": int(j[1] - (w - 1)),
                      "channel": name}
        # mirror symmetry
        for axis, label in ((1, "vertical"), (0, "horizontal")):
            zs, pos = C.mirror_z(ch, axis)
            if zs.size:
                k = int(np.argmax(zs))
                if zs[k] > mirror["z"]:
                    mirror = {"z": float(zs[k]), "axis": label, "pos": float(pos[k]), "channel": name}

    if "angle" in angled:
        angled["line"] = _angled_endpoints(h, w, angled["angle"], angled["row"])

    # tone: per-bin deviation from static's histogram, R/G/B
    exp = C.load_calibration()["tone_expected"]
    hist = C.tone_histogram(rgb).astype(float)
    total = hist.sum(axis=1, keepdims=True)
    e = (exp if exp is not None else np.full((3, 16), 1 / 16)) * total
    tone_z = (hist - e) / np.sqrt(e)

    # correlations
    pal = {}
    for a, b, key in ((0, 1, "red-green"), (1, 2, "green-blue"), (0, 2, "red-blue")):
        pal[key] = C._corr(rgb[..., a], rgb[..., b])
    y = ycc[..., 0]
    tex = {"horizontal": C._corr(y[:, :-1], y[:, 1:]), "vertical": C._corr(y[:-1, :], y[1:, :]),
           "diagonal": C._corr(y[:-1, :-1], y[1:, 1:]), "anti-diagonal": C._corr(y[:-1, 1:], y[1:, :-1])}

    r1 = lambda a: [round(float(v), 1) for v in np.ravel(a)]   # noqa: E731
    return {
        "size": [w, h], "cell": CELL, "grid": [int(mean_best.shape[1]), int(mean_best.shape[0])],
        "mean": r1(mean_best), "mean_channel": [int(v) for v in np.ravel(mean_ch)],
        "contrast": r1(contrast_best),
        "row_band": ROW_BAND, "rows": r1(rows_best), "cols": r1(cols_best),
        "angled": _round(angled), "repeat": _round(repeat), "mirror": _round(mirror),
        "tone": [r1(t) for t in tone_z],
        "palette": {k: round(v, 4) for k, v in pal.items()},
        "texture": {k: round(v, 4) for k, v in tex.items()},
        "defaults": LAYER_DEFAULTS,
    }


def _round(d: dict) -> dict:
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}


def _angled_endpoints(h: int, w: int, angle: float, row: float) -> list[float]:
    """Map a row of the rotated frame back to a segment in the original, by
    rotating coordinate images the same way the test did."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    rx, _ = C._rotated(xs, angle)
    ry, m = C._rotated(ys, angle)
    i = int(round(row))
    i = max(0, min(i, m.shape[0] - 1))
    valid = np.nonzero(m[i] > 0)[0]
    if valid.size < 2:
        return []
    a, b = valid[0], valid[-1]
    return [round(float(rx[i, a]), 1), round(float(ry[i, a]), 1),
            round(float(rx[i, b]), 1), round(float(ry[i, b]), 1)]


def tone_mask_png(image, maps: dict) -> bytes:
    """1-bit mask of the pixels in the most deviant colour range."""
    rgb = np.asarray(_load(image).convert("RGB"))
    tone = np.asarray(maps["tone"])
    c, b = np.unravel_index(int(np.argmax(np.abs(tone))), tone.shape)
    m = (rgb[..., c] >= b * 16) & (rgb[..., c] < (b + 1) * 16)
    buf = io.BytesIO()
    Image.fromarray(m.astype(np.uint8) * 255).convert("1").save(buf, "PNG", optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------------------
# descriptions
# --------------------------------------------------------------------------
def _where(x: float, y: float, w: int, h: int) -> str:
    col = ("left", "centre", "right")[min(2, int(3 * x / w))]
    row = ("top", "middle", "bottom")[min(2, int(3 * y / h))]
    if row == "middle" and col == "centre":
        return "the centre"
    if row == "middle":
        return f"the {col} side"
    if col == "centre":
        return f"the {row}"
    return f"the {row}-{col}"


def _how(sigma: float, faint: str = "faint", clear: str = "clear", strong: str = "strong") -> str:
    return faint if sigma < C.PICTURE_SIGMA * 2 else clear if sigma < 40 else strong


def line_angle(line: list[float]) -> int:
    """Visual angle of a segment, 0-179 degrees (0 = horizontal, counter-
    clockwise as seen on screen)."""
    x0, y0, x1, y1 = line
    return int(round(math.degrees(math.atan2(-(y1 - y0), x1 - x0)))) % 180


def _phrase(test: str, maps: dict, sigma: float) -> str:
    w, h = maps["size"]
    gw, gh = maps["grid"]
    cell = maps["cell"]
    s = f"{sigma:.1f}σ"
    if test in ("shape", "blob"):
        m = np.asarray(maps["mean"]).reshape(gh, gw)
        j = np.unravel_index(int(np.argmax(np.abs(m))), m.shape)
        ch = C.CHANNELS[np.asarray(maps["mean_channel"]).reshape(gh, gw)[j]]
        word = CHANNEL_WORDS[ch][0 if m[j] > 0 else 1]
        strong = int((np.abs(m) >= max(3.0, 0.5 * abs(m[j]))).sum())
        size = int(round(math.sqrt(max(strong, 1)) * cell / 8) * 8)
        what = "region" if test == "shape" else "spot"
        return (f"a {word} {what} around {_where((j[1] + .5) * cell, (j[0] + .5) * cell, w, h)}, "
                f"roughly {size} px across ({s})")
    if test == "contrast":
        m = np.asarray(maps["contrast"]).reshape(gh, gw)
        j = np.unravel_index(int(np.argmax(np.abs(m))), m.shape)
        kind = "noisier" if m[j] > 0 else "flatter (less varied)"
        return f"a patch around {_where((j[1] + .5) * cell, (j[0] + .5) * cell, w, h)} that is {kind} than static ({s})"
    if test == "lines":
        rows, cols = np.asarray(maps["rows"]), np.asarray(maps["cols"])
        band = maps["row_band"]
        if np.abs(rows).max() >= np.abs(cols).max():
            i = int(np.argmax(np.abs(rows)))
            return (f"a {'brighter' if rows[i] > 0 else 'darker'} horizontal band near "
                    f"y ≈ {int((i + .5) * band)} px ({s})")
        i = int(np.argmax(np.abs(cols)))
        return (f"a {'brighter' if cols[i] > 0 else 'darker'} vertical band near "
                f"x ≈ {int((i + .5) * band)} px ({s})")
    if test == "angled":
        a = maps["angled"]
        if not a.get("line"):
            return f"a faint line at an angle ({s})"
        x0, y0, x1, y1 = a["line"]
        word = CHANNEL_WORDS[a["channel"]][0 if a["z"] > 0 else 1]
        return (f"a {_how(sigma)} {word} line at {line_angle(a['line'])}° through "
                f"{_where((x0 + x1) / 2, (y0 + y1) / 2, w, h)} ({s})")
    if test == "repetition":
        r = maps["repeat"]
        return f"part of the frame repeats at an offset of ({r['dx']}, {r['dy']}) px ({s})"
    if test == "symmetry":
        m = maps["mirror"]
        coord = "x" if m["axis"] == "vertical" else "y"
        return f"{_how(sigma, 'a hint of', 'clear', 'strong')} mirror symmetry about a {m['axis']} line at {coord} ≈ {int(m['pos'])} px ({s})"
    if test == "tone":
        t = np.asarray(maps["tone"])
        c, b = np.unravel_index(int(np.argmax(np.abs(t))), t.shape)
        more = "more" if t[c, b] > 0 else "fewer"
        amount = _how(sigma, "slightly", "noticeably", "far")
        return (f"{amount} {more} {TONE_RANGES[b]} {RGB[c]} values ({b * 16}–{b * 16 + 15}) than static "
                f"produces ({s})")
    if test == "palette":
        p = maps["palette"]
        k = max(p, key=lambda q: abs(p[q]))
        return (f"the {k} channels move {'together' if p[k] > 0 else 'in opposition'} "
                f"(r = {p[k]:+.3f}; static gives ≈ 0) ({s})")
    if test == "texture":
        t = maps["texture"]
        k = max(t, key=lambda q: abs(t[q]))
        kind = "smoother: neighbouring pixels agree" if t[k] > 0 else "alternating: neighbouring pixels disagree"
        return f"{kind} more than static, mostly {k}ly (r = {t[k]:+.3f}) ({s})"
    return f"{test} ({s})"


GLOBAL_TESTS = {"tone", "palette", "texture"}


def describe(tests: dict, maps: dict, sigma: float) -> str:
    """One or two sentences about what makes this frame unusual."""
    ranked = sorted(tests.items(), key=lambda kv: kv[1], reverse=True)
    top, top_z = ranked[0]
    lead = ("Statistically unusual static, not a picture: " if sigma < C.PICTURE_SIGMA
            else "Structure well beyond static: ")
    text = lead + _phrase(top, maps, top_z) + "."
    also = [(t, z) for t, z in ranked[1:3] if z >= 3.0]
    if also:
        text += " Also " + "; ".join(_phrase(t, maps, z) for t, z in also) + "."
    elif top in GLOBAL_TESTS:
        text += " Nothing local: no shapes, lines or patterns stand out."
    return text
