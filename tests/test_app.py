import io

from PIL import Image

from app import create_app
from babel_surfer.scraper import pick_seed


def png(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, "PNG")
    return buf.getvalue()


def test_store_and_api(tmp_path):
    app = create_app(str(tmp_path))
    store = app.config["store"]
    fid = store.save_find(seed="abc123", image_bytes=png(), ext="png",
                          score=9.0, label="structure", metrics={"score": 9.0})
    assert store.save_find(seed="dup", image_bytes=png(), ext="png", score=1,
                           label="x", metrics={}) is None  # dedupe by hash
    c = app.test_client()
    finds = c.get("/api/finds").get_json()
    assert [f["seed"] for f in finds] == ["abc123"]
    assert c.get(f"/images/{finds[0]['image_file']}").data == png()
    assert c.get("/").status_code == 200
    assert c.get("/api/status").get_json()["totals"]["finds"] == 1
    assert c.delete(f"/api/finds/{fid}").status_code == 204
    assert c.get("/api/finds").get_json() == []


def test_classify_endpoint(tmp_path):
    c = create_app(str(tmp_path)).test_client()
    r = c.post("/api/classify", data={"image": (io.BytesIO(png()), "x.png")})
    assert r.get_json()["is_interesting"] is True


def test_pick_seed():
    assert pick_seed(["https://x/y.cgi?location=0abc"], None, None) == "0abc"
    assert pick_seed(["short", "a-much-longer-seed-value"], None, None) == "a-much-longer-seed-value"
    assert pick_seed([], "https://x/img.cgi?seed=42", None) == "42"
    assert pick_seed([], None, "https://x/slideshow.html#deadbeef") == "deadbeef"
    assert pick_seed([], None, None) == "unknown"
