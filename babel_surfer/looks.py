"""The "looks" test: does the frame look like static to a vision model?
(Plan item B1.)

The other tests each look for one kind of structure. This one asks a
general-purpose image model (CLIP ViT-B/32, run on the CPU with
onnxruntime) what the frame looks like, and measures how far its answer is
from what static gives. Raw value: the frame's CLIP embedding, whitened by
static's own spread (probabilistic PCA: the top `K` directions of static's
variation each scaled by its variance, the rest pooled), as a squared
distance. On static it is about 512 on average; anything image-like moves
the embedding away from static's.

Why it is worth an extra test:
- It is a second line of defence built on a different principle. A weak
  point in a hand-made test (`marks` on drawn strokes, say) is unlikely to
  be a weak point of a learned model too.
- It reacts to things nobody wrote a test for.
- It can say what a frame looks like (`describe`), using the model's own
  words for a few dozen everyday things.

It is not more sensitive than the hand-made tests: on the probe's families
they fire well below the strength where this one does (measured
2026-11-23). Its null is fitted on simulated static (`fit`), which CLIP
cannot tell from real Babelia frames: on 187 real frames the mean shift was
within sampling noise and the distance had the same spread. The score log
then refits the map on real frames like any new test.

The model (89 MB) is downloaded once, from a pinned revision, and checked
against its SHA-256 before use. Set SURFING_BABEL_MODELS to choose where it
is kept (default ~/.cache/surfing-babel).

  python -m babel_surfer.looks download      # fetch and verify the model
  python -m babel_surfer.looks fit           # rebuild looks_basis.npz (needs the text model too)
  python -m babel_surfer.looks score FRAME.jpg
"""

from __future__ import annotations

import argparse
import hashlib
import io
import math
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

REPO = "Xenova/clip-vit-base-patch32"
REVISION = "6b7c523703ae26e39be4b03774966f8571c7f523"
MODELS = {
    "vision": ("onnx/vision_model_quantized.onnx", 89_117_001,
               "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299"),
    # only for `fit` (the prompts' embeddings are stored in the basis)
    "text": ("onnx/text_model_quantized.onnx", 64_504_507,
             "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a"),
    "tokenizer": ("tokenizer.json", 2_224_119, None),
}
BASIS_FILE = Path(__file__).with_name("looks_basis.npz")
SIZE = 224
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
K = 32                    # whitened directions (16-64 measured; 32 was best on the probe)
FIT_FRAMES = 3000
FIT_SEED = 20261123
PROMPTS = ("a photo", "a drawing", "a painting", "a shape", "a circle", "a square", "a line",
           "lines", "a curve", "text", "letters", "a word", "a face", "a person", "a gradient",
           "a pattern", "stripes", "a grid", "a checkerboard", "dots", "a landscape", "the sky",
           "a logo", "a symbol", "a blob of colour", "a dark patch", "a bright patch", "rings")
USER_AGENT = "SurfingBabel/1.0"

_session = None
_basis = None


def model_dir() -> Path:
    return Path(os.environ.get("SURFING_BABEL_MODELS") or Path.home() / ".cache" / "surfing-babel")


def model_path(name: str = "vision") -> Path:
    return model_dir() / REVISION[:12] / Path(MODELS[name][0]).name


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(name: str = "vision") -> Path:
    """Fetch one model file from the pinned revision unless it is already
    there, and check its size and hash. A file that fails the check is
    deleted, never used."""
    path, size, sha = MODELS[name]
    out = model_path(name)
    if out.exists() and out.stat().st_size == size and (sha is None or _sha256(out) == sha):
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{path}"
    tmp = out.with_suffix(out.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    if tmp.stat().st_size != size or (sha is not None and _sha256(tmp) != sha):
        tmp.unlink()
        raise RuntimeError(f"{url}: wrong size or SHA-256; not used")
    tmp.replace(out)
    return out


def _vision():
    global _session
    if _session is None:
        import onnxruntime as ort
        p = model_path("vision")
        if not p.exists():
            raise RuntimeError(f"the looks test needs the vision model at {p}: "
                               "run `python -m babel_surfer.looks download`")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1          # frames are scored in parallel processes
        opts.inter_op_num_threads = 1
        _session = ort.InferenceSession(str(p), opts, providers=["CPUExecutionProvider"])
    return _session


def pixels(image: Image.Image) -> np.ndarray:
    """CLIP's input: the whole frame resized to 224 x 224 (not cropped:
    a crop would hide the frame's edges), scaled and normalised."""
    im = image.convert("RGB").resize((SIZE, SIZE), Image.BICUBIC)
    x = (np.asarray(im, dtype=np.float32) / 255.0 - MEAN) / STD
    return x.transpose(2, 0, 1)[None]


def embed(image: Image.Image | bytes) -> np.ndarray:
    if isinstance(image, (bytes, bytearray)):
        image = Image.open(io.BytesIO(image))
    e = _vision().run(None, {"pixel_values": pixels(image)})[0][0].astype(np.float64)
    return e / np.linalg.norm(e)


def basis() -> dict:
    global _basis
    if _basis is None:
        with np.load(BASIS_FILE) as z:
            _basis = {k: z[k] for k in z.files}
    return _basis


def distance(e: np.ndarray, b: dict | None = None) -> float:
    """Whitened squared distance of a unit embedding from static's."""
    b = b or basis()
    d = e - b["mean"]
    c = b["components"] @ d
    rest = float(d @ d - c @ c)
    return float((c * c / b["variance"]).sum() + rest / float(b["rest_variance"]))


def raw(image: Image.Image) -> float:
    return math.log(distance(embed(image)))


def describe(image: Image.Image | bytes, top: int = 2) -> list[tuple[str, float]]:
    """What the model thinks the frame looks like: the prompts whose match
    rises most above what they get on static, in static's standard
    deviations. Only meaningful for frames the test scores highly."""
    b = basis()
    sims = b["prompt_embeddings"] @ embed(image)
    z = (sims - b["prompt_mean"]) / b["prompt_sd"]
    order = np.argsort(-z)[:top]
    return [(str(b["prompts"][i]), round(float(z[i]), 1)) for i in order]


def _static_embedding(seed: int) -> np.ndarray:
    from .calibrate import babel_static
    return embed(babel_static(np.random.default_rng(seed)))


def _text_embeddings(prompts) -> np.ndarray:
    import onnxruntime as ort
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(str(download("tokenizer")))
    sess = ort.InferenceSession(str(download("text")), providers=["CPUExecutionProvider"])
    out = []
    for p in prompts:
        e = sess.run(None, {"input_ids": np.array([tok.encode(p).ids], dtype=np.int64)})[0][0]
        out.append(e / np.linalg.norm(e))
    return np.array(out)


def fit(out: Path = BASIS_FILE, frames: int = FIT_FRAMES, seed: int = FIT_SEED, workers=None) -> dict:
    """Static's embedding mean and spread, from simulated static, plus the
    prompts' embeddings and how they match static."""
    from concurrent.futures import ProcessPoolExecutor
    download("vision")
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        E = np.array(list(pool.map(_static_embedding, range(seed, seed + frames), chunksize=16)))
    mean = E.mean(axis=0)
    _, sv, vt = np.linalg.svd(E - mean, full_matrices=False)
    var = sv ** 2 / (len(E) - 1)
    T = _text_embeddings(PROMPTS)
    S = E @ T.T
    b = {"mean": mean, "components": vt[:K], "variance": var[:K],
         "rest_variance": np.float64(var[K:].sum() / (E.shape[1] - K)),
         "prompts": np.array(PROMPTS), "prompt_embeddings": T,
         "prompt_mean": S.mean(axis=0), "prompt_sd": S.std(axis=0),
         "frames": np.int64(frames), "seed": np.int64(seed), "revision": np.array(REVISION)}
    np.savez_compressed(out, **{k: (v.astype(np.float32) if isinstance(v, np.ndarray) and v.dtype == np.float64
                                    else v) for k, v in b.items()})
    global _basis
    _basis = None
    d = np.array([distance(e, b) for e in E])
    return {"frames": frames, "mean_distance": round(float(d.mean()), 1), "sd": round(float(d.std()), 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("download")
    f = sub.add_parser("fit")
    f.add_argument("--frames", type=int, default=FIT_FRAMES)
    s = sub.add_parser("score")
    s.add_argument("frames", nargs="+", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "download":
        print(download("vision"))
    elif a.cmd == "fit":
        print(fit(frames=a.frames))
    else:
        for p in a.frames:
            im = Image.open(p)
            print(f"{p}: distance {math.exp(raw(im)):.1f}; looks like {describe(im)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
