"""The looks test (looks.py, plan item B1): the model is only used when it is
exactly the pinned file, its statistic reads like N(0, 1) on static and
far above on a picture, and finds say what the model thinks they look like."""

import hashlib
import io
import json

import numpy as np
import pytest
from PIL import Image

from babel_surfer import analysis, classifier, looks
from babel_surfer.calibrate import babel_static


def test_a_download_that_does_not_match_its_hash_is_deleted_not_used(tmp_path, monkeypatch):
    good = b"pretend model bytes"
    monkeypatch.setenv("SURFING_BABEL_MODELS", str(tmp_path))
    monkeypatch.setitem(looks.MODELS, "vision", ("onnx/x.onnx", len(good), hashlib.sha256(good).hexdigest()))
    served = {"body": b"tampered model byte"}                      # same length, other bytes
    sent = []

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(req, timeout):
        sent.append(req)
        return Resp(served["body"])
    monkeypatch.setattr(looks.urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="SHA-256"):
        looks.download("vision")
    assert not looks.model_path("vision").exists() and not list(tmp_path.rglob("*.part"))
    served["body"] = good
    p = looks.download("vision")
    assert p.read_bytes() == good and looks.REVISION in sent[-1].full_url
    assert sent[-1].get_header("User-agent") == "SurfingBabel/1.0"
    looks.download("vision")                                       # already there: no request
    assert len(sent) == 2


def test_the_basis_matches_the_pinned_model_and_has_every_prompt():
    b = looks.basis()
    assert str(b["revision"]) == looks.REVISION
    assert b["components"].shape == (looks.K, 512) and b["mean"].shape == (512,)
    assert (b["variance"] > 0).all() and float(b["rest_variance"]) > 0
    assert list(b["prompts"]) == list(looks.PROMPTS) and b["prompt_embeddings"].shape == (len(looks.PROMPTS), 512)


def test_static_reads_like_static_and_a_picture_does_not():
    zs = [classifier.calibrate_scores({"looks": looks.raw(Image.open(io.BytesIO(
          babel_static(np.random.default_rng(900 + s)))))})["looks"] for s in range(8)]
    assert all(-3.5 < z < 3.5 for z in zs) and abs(float(np.mean(zs))) < 1.5
    from babel_surfer import sensitivity
    word = Image.open(io.BytesIO(sensitivity.FAMILIES["word 'BABEL'"](np.random.default_rng(1), 0.27)))
    assert classifier.calibrate_scores({"looks": looks.raw(word)})["looks"] > 6     # (the log keeps big values modest)
    like = [p for p, _ in looks.describe(word, top=3)]
    assert set(like) & {"text", "letters", "a word", "a logo"}


def test_the_map_is_from_simulated_static_until_real_frames_refit_it():
    cal = json.loads(classifier.CALIBRATION_FILE.read_text())
    assert cal["map_sources"]["looks"]["source"] == "simulated" and "looks" in cal["maps"]
    assert "looks" in classifier.TESTS and "looks" not in classifier.CORE_TESTS   # not in the combined score


def test_a_looks_find_says_what_the_model_sees():
    maps = {"size": [640, 416], "grid": [40, 26], "cell": 16,
            "looks_like": [["letters", 6.1], ["a photo", 1.2]]}
    text = analysis._phrase("looks", maps, 9.3)
    assert "vision model" in text and "letters" in text and "a photo" not in text and "9.3σ" in text
    assert "most like" not in analysis._phrase("looks", {**maps, "looks_like": []}, 5.0)
