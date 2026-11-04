"""End-to-end: run the real scraper against a local stand-in slideshow page."""
import functools
import http.server
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright")
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(FIXTURES))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/slideshow.html"
    httpd.shutdown()


def test_scraper_finds_shapes_and_skips_static(server, tmp_path, monkeypatch):
    from babel_surfer import scraper as mod
    from babel_surfer.storage import Store

    monkeypatch.setattr(mod, "SLIDESHOW_URL", server)
    store = Store(tmp_path)
    s = mod.Scraper(store, interval=0.1)
    s.start()
    deadline = time.time() + 30
    while time.time() < deadline and len(store.list_finds()) < 2:
        time.sleep(0.3)
    s.stop()
    s._thread.join(15)
    finds = store.list_finds()
    assert len(finds) >= 2, s.state
    assert all(f["seed"].endswith("-shape") for f in finds), [f["seed"] for f in finds]
    assert store.stats()["scanned"] > len(finds)
    for f in finds:
        assert (store.image_dir / f["image_file"]).stat().st_size > 1000
