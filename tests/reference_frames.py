"""Deterministic frames for the detector regression test: simulated static
plus a few faint synthetic signals (regenerated from seeds, so no binary
fixtures are stored)."""

import numpy as np

from babel_surfer import benchmark as B
from babel_surfer.calibrate import babel_static


def reference_frames():
    out = [(f"static-{s}", babel_static(np.random.default_rng(1000 + s))) for s in range(12)]
    rng = np.random.default_rng(77)
    for name, (maker, alpha) in {"disc": (B.pic_disc, 0.05), "landscape": (B.pic_landscape, 0.05),
                                 "line": (B.pic_line, 0.25), "square": (B.pic_square, 0.25)}.items():
        pic, mask = maker()
        out.append((name, B._blend(rng, pic, mask, alpha)))
    for name, fn, alpha in (("grey", B._grey_patch, 0.25), ("diag", B._diagonal_line, 0.25),
                            ("tiled", B._tiled, 0.2), ("mirror", B._mirrored, 0.2)):
        out.append((name, fn(rng, alpha)))
    return out
