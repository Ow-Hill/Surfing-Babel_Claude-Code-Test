"""The rating kit that checks the visibility model against people (plan item
B4, rate.py): the set it writes, the agreement it reports and the cut-offs
it fits. Frames are stubbed (real ones take a minute and 25 MB); the page is
driven in Chromium by the browser job."""

import json

import numpy as np
import pytest

from babel_surfer import rate


@pytest.fixture
def kit(tmp_path, monkeypatch):
    """A 20-frame set whose model ratios are 1.0, 1.25, ... 5.75."""
    ratios = iter(np.arange(20) * 0.25 + 1.0)
    monkeypatch.setattr(rate, "items", lambda n, seed: [("static" if i < 3 else "large disc", 0.1, b"\xff\xd8 %d" % i)
                                                         for i in range(n)])
    monkeypatch.setattr(rate, "_ratio", lambda data: float(next(ratios)))
    out = tmp_path / "set"
    assert rate.main(["make", str(out), "--frames", "20", "--seed", "4"]) == 0
    return out


def _rater(out, tmp_path, name, see_from, unsure=()):
    key = json.loads((out / "key.json").read_text())["items"]
    r = {str(k["id"]): ("unsure" if k["id"] in unsure else "seen" if k["ratio"] >= see_from else "nothing")
         for k in key}
    f = tmp_path / f"ratings-{name}.json"
    f.write_text(json.dumps({"rater": name, "set": "20-4", "ratings": r}))
    return f


def test_the_set_hides_the_answers_from_the_page(kit):
    page = (kit / "index.html").read_text()
    key = json.loads((kit / "key.json").read_text())
    assert key["set"] == "20-4" and len(key["items"]) == 20 and len(list((kit / "frames").glob("*.jpg"))) == 20
    assert "ratio" not in page and "large disc" not in page and '"frames/000.jpg"' in page


def test_fit_measures_agreement_and_finds_the_raters_cut_off(kit, tmp_path, capsys):
    """Three raters who see everything from ratio 3.0 (one is unsure about
    two frames): the model's 2.0 cut-off calls four frames visible that most
    raters don't see, and the best cut-off lies between 2.75 and 3.0."""
    files = [_rater(kit, tmp_path, "a", 3.0), _rater(kit, tmp_path, "b", 3.0),
             _rater(kit, tmp_path, "c", 3.0, unsure=(0, 1))]
    r = rate.fit(kit, files)
    assert r["frames"] == 20 and r["raters"] == ["a", "b", "c"]
    assert r["visible"]["model_agreement"] == 0.8 and r["visible"]["fitted_agreement"] == 1.0
    assert 2.75 < r["visible"]["fitted_cut"] <= 3.0
    assert r["rater_agreement"] == 1.0                     # "unsure" counts as not seen
    assert r["static_seen"] == 0 and r["static_frames"] == 3
    assert rate.main(["fit", str(kit), *map(str, files), "--json", str(tmp_path / "r.json")]) == 0
    assert "best cut" in capsys.readouterr().out and json.loads((tmp_path / "r.json").read_text()) == r


def test_ratings_for_another_set_are_refused(kit, tmp_path):
    f = _rater(kit, tmp_path, "a", 3.0)
    f.write_text(f.read_text().replace('"20-4"', '"50-0"'))
    with pytest.raises(ValueError, match="rates set 50-0"):
        rate.fit(kit, [f])


@pytest.mark.browser
def test_the_rating_page_records_keys_and_saves_a_file_fit_can_read(kit, tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    from test_site_browser import CHROMIUM
    with sync_api.sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else pw.chromium.launch()
        page = b.new_page()
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith("file:")
                   else route.abort())
        page.goto((kit / "index.html").as_uri())
        page.fill("#rater", "tester")
        page.locator("body").click(position={"x": 5, "y": 5})
        for k in ["1", "3", "2"] + ["3"] * 17:
            page.keyboard.press(k)
        assert "All rated" in page.text_content("#done")
        with page.expect_download() as dl:
            page.click("#save")
        saved = tmp_path / "ratings-tester.json"
        dl.value.save_as(saved)
        b.close()
    got = json.loads(saved.read_text())
    assert got["rater"] == "tester" and got["set"] == "20-4" and len(got["ratings"]) == 20
    assert got["ratings"]["0"] == "seen" and got["ratings"]["2"] == "unsure"
    assert rate.fit(kit, [saved])["frames"] == 20
