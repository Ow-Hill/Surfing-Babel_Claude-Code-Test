"""Check the visibility model against people (plan item B4).

The visibility model (visibility.py) decides what counts as "visible", and
so what the blind-spot probe calls a blind spot. Its cut-offs (2.0 likely
visible, 1.4 faint) were set by eye, by one person. This kit measures them
against a few raters:

  python -m babel_surfer.rate make out/rating        # 50 frames + a rating page
  (each rater opens out/rating/index.html, rates every frame, saves the file)
  python -m babel_surfer.rate fit out/rating ratings-*.json

`make` writes the frames (pure static, and the probe's signal families at
strengths around where a person starts to see them), a self-contained
rating page that works offline (open the file in any browser), and
`key.json`, which holds each frame's signal, strength and model ratio. The
key is not shown on the page. `fit` reports how often the model agrees with
the raters' majority, how often raters agree with each other, and the
cut-offs that would agree best. It does not change the model: if the fitted
cut-offs differ, change visibility.label by hand, and say why.

No requests to Babelia: everything is simulated.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from .visibility import FAINT, LIKELY as VISIBLE     # the model's cut-offs (visibility.label)

STATIC_SHARE = 0.16               # frames that are pure static (a check on the raters)
STRENGTHS = (0.02, 0.6)           # signal strengths drawn log-uniformly between these
SCORE = {"seen": 1.0, "unsure": 0.5, "nothing": 0.0}


def _static(rng) -> bytes:
    from .calibrate import babel_static
    return babel_static(rng)


def items(n: int = 50, seed: int = 0):
    """(signal, strength, JPEG bytes) for n frames, in a fixed random order."""
    from .sensitivity import FAMILIES
    rng = np.random.default_rng(seed)
    names = sorted(FAMILIES)
    out = []
    for i in range(n):
        r = np.random.default_rng(seed * 100_003 + i)
        if i < round(n * STATIC_SHARE):
            out.append(("static", 0.0, _static(r)))
            continue
        name = names[int(rng.integers(len(names)))]
        s = float(math.exp(rng.uniform(math.log(STRENGTHS[0]), math.log(STRENGTHS[1]))))
        out.append((name, round(s, 4), FAMILIES[name](r, s)))
    order = rng.permutation(n)
    return [out[k] for k in order]


def _ratio(data: bytes) -> float:
    from .visibility import visibility
    return visibility(np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float64))["ratio"]


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rate frames</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#ddd;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#121212;--fg:#eee;--muted:#aaa;--line:#333;--accent:#60a5fa}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}
img{width:100%;height:auto;display:block;border:1px solid var(--line);image-rendering:pixelated}
.row{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
button{flex:1;min-width:120px;padding:12px;font:inherit;border:1px solid var(--line);border-radius:8px;
background:transparent;color:var(--fg);cursor:pointer}
button:hover,button:focus-visible{border-color:var(--accent)}
.muted{color:var(--muted)} input{font:inherit;padding:8px;width:100%;box-sizing:border-box}
</style></head><body><main>
<h1>Rate frames</h1>
<p class="muted">Each frame is random static, and some have something hidden in them. Look at each one for a few
seconds at full size and say whether you can see anything that is not static: a shape, line, patch, text,
gradient or pattern. Answer by eye; don't zoom in or edit the image. Keys: 1 = I see something, 2 = not sure,
3 = nothing.</p>
<p><label>Your name or initials <input id="rater" autocomplete="off"></label></p>
<p id="count" class="muted"></p>
<img id="frame" alt="frame to rate">
<div class="row"><button data-r="seen">1 · I see something</button><button data-r="unsure">2 · Not sure</button>
<button data-r="nothing">3 · Nothing</button></div>
<div class="row"><button id="back">Back</button><button id="save">Save my ratings</button></div>
<p id="done" class="muted"></p>
</main><script>
const ITEMS = __ITEMS__;
const ratings = {}; let i = 0;
const $ = id => document.getElementById(id);
function show() {
  i = Math.max(0, Math.min(i, ITEMS.length - 1));
  $("frame").src = ITEMS[i].file;
  const n = Object.keys(ratings).length;
  $("count").textContent = `Frame ${i + 1} of ${ITEMS.length} · ${n} rated` +
    (ratings[ITEMS[i].id] ? ` · your answer: ${ratings[ITEMS[i].id]}` : "");
  $("done").textContent = n === ITEMS.length ? "All rated. Save your ratings and send the file back." : "";
}
function rate(r) { ratings[ITEMS[i].id] = r; if (i < ITEMS.length - 1) i++; show(); }
document.querySelectorAll("[data-r]").forEach(b => b.onclick = () => rate(b.dataset.r));
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT") return;
  const r = {"1": "seen", "2": "unsure", "3": "nothing"}[e.key]; if (r) rate(r);
});
$("back").onclick = () => { i--; show(); };
$("save").onclick = () => {
  const rater = $("rater").value.trim() || "anonymous";
  const blob = new Blob([JSON.stringify({rater, set: __SET__, ratings}, null, 1)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = `ratings-${rater.replace(/[^\\w-]+/g, "_")}.json`; a.click();
};
show();
</script></body></html>
"""


def make(out: Path, n: int = 50, seed: int = 0) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "frames").mkdir(exist_ok=True)
    shown, key = [], []
    for k, (name, strength, data) in enumerate(items(n, seed)):
        file = f"frames/{k:03d}.jpg"
        (out / file).write_bytes(data)
        shown.append({"id": k, "file": file})
        key.append({"id": k, "signal": name, "strength": strength, "ratio": _ratio(data)})
    set_id = f"{n}-{seed}"
    (out / "key.json").write_text(json.dumps({"set": set_id, "items": key}, indent=1))
    page = PAGE.replace("__ITEMS__", json.dumps(shown)).replace("__SET__", json.dumps(set_id))
    (out / "index.html").write_text(page)
    return {"set": set_id, "frames": n}


def _accuracy(ratio: np.ndarray, truth: np.ndarray, cut: float) -> float:
    return float(((ratio >= cut) == truth).mean())


def best_cut(ratio: np.ndarray, truth: np.ndarray) -> tuple[float, float]:
    """The cut-off on the model's ratio that best separates truth; ties go to
    the cut nearest the middle of the run of equally good ones."""
    cands = np.unique(np.concatenate([ratio, [ratio.max() + 1]]))
    acc = np.array([_accuracy(ratio, truth, c) for c in cands])
    good = cands[acc == acc.max()]
    mid = good[len(good) // 2]
    below = ratio[ratio < mid]
    cut = (mid + below.max()) / 2 if len(below) else mid     # halfway between neighbouring ratios
    return round(float(cut), 2), round(float(acc.max()), 3)


def fit(set_dir: Path, rating_files) -> dict:
    key = json.loads((set_dir / "key.json").read_text())
    ids = [k["id"] for k in key["items"]]
    ratio = np.array([k["ratio"] for k in key["items"]], dtype=float)
    raters = []
    for f in rating_files:
        r = json.loads(Path(f).read_text())
        if r.get("set") != key["set"]:
            raise ValueError(f"{f} rates set {r.get('set')}, not {key['set']}")
        raters.append((r.get("rater", Path(f).stem), r["ratings"]))
    if not raters:
        raise ValueError("no ratings")
    score = np.full((len(raters), len(ids)), np.nan)
    for a, (_, rr) in enumerate(raters):
        for j, i in enumerate(ids):
            if str(i) in rr:
                score[a, j] = SCORE[rr[str(i)]]
    rated = ~np.isnan(score).all(axis=0)
    mean = np.nanmean(np.where(np.isnan(score), np.nan, score)[:, rated], axis=0)
    ratio = ratio[rated]
    seen = mean > 0.5                  # most raters see it
    any_seen = mean > 0                # someone sees or suspects it
    pair = []
    for a in range(len(raters)):
        for b in range(a + 1, len(raters)):
            both = ~np.isnan(score[a]) & ~np.isnan(score[b])
            if both.any():
                pair.append(float(((score[a][both] > 0.5) == (score[b][both] > 0.5)).mean()))   # unsure = not seen, as in the majority
    static = np.array([k["signal"] == "static" for k in key["items"]])[rated]
    vis_cut, vis_acc = best_cut(ratio, seen)
    faint_cut, faint_acc = best_cut(ratio, any_seen)
    return {"set": key["set"], "raters": [r for r, _ in raters], "frames": int(rated.sum()),
            "rater_agreement": round(float(np.mean(pair)), 3) if pair else None,
            "static_seen": int((seen & static).sum()), "static_frames": int(static.sum()),
            "visible": {"model_cut": VISIBLE, "model_agreement": round(_accuracy(ratio, seen, VISIBLE), 3),
                        "fitted_cut": vis_cut, "fitted_agreement": vis_acc},
            "faint": {"model_cut": FAINT, "model_agreement": round(_accuracy(ratio, any_seen, FAINT), 3),
                      "fitted_cut": faint_cut, "fitted_agreement": faint_acc}}


def report(r: dict) -> str:
    out = [f"{r['frames']} frames, {len(r['raters'])} raters ({', '.join(r['raters'])})"]
    if r["rater_agreement"] is not None:
        out.append(f"raters agree with each other on {r['rater_agreement']:.0%} of frames")
    out.append(f"pure static that most raters 'saw': {r['static_seen']} of {r['static_frames']}")
    for k, what in (("visible", "most raters see it"), ("faint", "anyone sees or suspects it")):
        v = r[k]
        out.append(f"{k:8s} ({what}): model cut {v['model_cut']} agrees on {v['model_agreement']:.0%}; "
                   f"best cut {v['fitted_cut']} agrees on {v['fitted_agreement']:.0%}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="write a rating set")
    m.add_argument("out", type=Path)
    m.add_argument("--frames", type=int, default=50)
    m.add_argument("--seed", type=int, default=0)
    f = sub.add_parser("fit", help="compare ratings with the model")
    f.add_argument("set", type=Path)
    f.add_argument("ratings", nargs="+", type=Path)
    f.add_argument("--json", type=Path, help="also write the result here")
    a = ap.parse_args(argv)
    if a.cmd == "make":
        r = make(a.out, a.frames, a.seed)
        print(f"wrote {r['frames']} frames to {a.out}; raters open {html.escape(str(a.out / 'index.html'))}")
        return 0
    r = fit(a.set, a.ratings)
    if a.json:
        a.json.write_text(json.dumps(r, indent=1))
    print(report(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
