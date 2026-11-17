"""The built site in a real (headless) browser: it loads without errors,
never scrolls sideways on a phone, a find opens with its chance line, the
search demo runs, dark mode renders, interactive elements have names, and
the page stays within a size budget.

Runs where Playwright and Chromium are installed (the Tests workflow's
`site` job); skipped elsewhere. The page is served from localhost and every
other request is blocked, so no test reaches Babelia or anywhere else.
"""

import functools
import http.server
import json
import os
import socketserver
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

CHROMIUM = os.environ.get("PW_CHROMIUM") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)
OPTIONAL = ("data/sensitivity.json", "demo/demo.json", "benchmark/benchmark.json", "data/flags.json")
BUDGET_CODE = 250_000          # bytes of HTML, JS and CSS on first load


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """A real build: frames scored by the real detector and kept by the real
    store, so the data files have the format the site reads."""
    from babel_surfer import benchmark as B, build_site, demo_kit
    from babel_surfer.calibrate import babel_static
    from babel_surfer.classifier import classify
    from babel_surfer.storage import Store
    out = tmp_path_factory.mktemp("site") / "public"
    store = Store(out / "data")
    rng = np.random.default_rng(3)
    run = {"started_at": time.time() - 1800, "scanned": 0, "finds": 0, "errors": 0}
    picture, mask = B.SIGNALS["large disc"][0]()
    frames = [B._blend(rng, picture, mask, 0.1)] + [babel_static(rng) for _ in range(5)]
    for i, data in enumerate(frames):
        store.record(str(10**15 + i), data, classify(data), run)
    run["finished_at"] = time.time()
    store.add_run(run)
    store.save()
    real = demo_kit.spec_results
    demo_kit.spec_results = lambda: {"variant": "stub", "status": "waiting", "requirements": []}
    try:
        build_site.build(out, network=False, bench="skip")
    finally:
        demo_kit.spec_results = real
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(out))
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else pw.chromium.launch()
        except Exception as e:                      # no browser installed here
            pytest.skip(f"no Chromium: {e}")
        yield b
        b.close()


def open_page(browser, site, **opts):
    page = browser.new_page(**opts)
    problems, sizes = [], {}
    local = site.rstrip("/")

    def route(r):
        if r.request.url.startswith(local):
            r.continue_()
        else:
            r.abort()                               # never the internet
    page.route("**/*", route)
    page.on("pageerror", lambda e: problems.append(f"error: {e}"))

    def on_response(resp):
        path = resp.url[len(local) + 1:].split("?")[0]
        if resp.status >= 400 and path not in OPTIONAL:
            problems.append(f"{resp.status} {path}")
        if resp.status < 400 and path.endswith((".js", ".css", ".html")) or path == "":
            try:
                sizes[path] = len(resp.body())
            except Exception:
                pass
    page.on("response", on_response)
    page.goto(site + "index.html")
    page.wait_for_function("document.getElementById('s-scanned').textContent !== '–'")
    return page, problems, sizes


@pytest.mark.parametrize("width", [390, 1200])
def test_loads_cleanly_and_never_scrolls_sideways(browser, site, width):
    page, problems, _ = open_page(browser, site, viewport={"width": width, "height": 900})
    assert problems == []
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.text_content("#s-scanned") == "6"
    assert "chance alone" not in page.text_content("#s-best-chance")       # too few frames to say
    page.close()


def test_a_find_opens_with_how_often_chance_gives_it(browser, site):
    page, problems, _ = open_page(browser, site, viewport={"width": 1200, "height": 900})
    page.click("#finds > button")
    page.wait_for_selector("#detail[open]")
    assert page.is_visible("#d-sigma") and "σ" in page.text_content("#d-sigma")
    chance = page.text_content("#d-chance")            # the strong disc: far beyond chance
    assert "far beyond chance" in chance and "10^" in chance
    page.keyboard.press("Escape")
    page.wait_for_selector("#detail[open]", state="detached")
    page.click("#near > button")                         # static: no stale line from the disc
    page.wait_for_selector("#detail[open]")
    assert not page.is_visible("#d-chance") or "far beyond" not in page.text_content("#d-chance")
    # the wording at the live site's scale (528,000 frames, 53,000 a day)
    lines = page.evaluate("""() => { DATA.scanned = 528000; DATA.perDay = 53000;
                                    return [4.2, 5.6, 96].map(chanceLine); }""")
    assert "about once every" in lines[0] and "expected by chance" in lines[0]
    assert "rare" in lines[1] and "expected by chance" not in lines[1]
    assert "far beyond chance" in lines[2] and "Infinity" not in " ".join(lines)
    page.keyboard.press("Escape")
    assert problems == []
    page.close()


def test_the_search_demo_steps_and_scans(browser, site):
    page, problems, _ = open_page(browser, site, viewport={"width": 390, "height": 900})
    for _ in range(4):
        page.click("#sd-next")
    assert page.text_content("#sd-step-title").startswith("5.")
    page.click("#sd-batch")
    page.wait_for_function("document.getElementById('sd-batch-out').textContent.startsWith('1,000')",
                           timeout=60_000)
    assert "chance predicts" in page.text_content("#sd-batch-out")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390 and problems == []
    page.close()


def test_dark_mode_renders(browser, site):
    page, problems, _ = open_page(browser, site, viewport={"width": 1200, "height": 900},
                                  color_scheme="dark")
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg != "rgb(245, 242, 234)" and problems == []                  # not the light background
    page.close()


def test_every_control_has_a_name_and_every_image_an_alt(browser, site):
    """A small accessibility check (axe-core would need fetching from a CDN,
    which these offline tests cannot): buttons, links and form fields are
    named for screen readers, images say what they are or are marked as
    decoration, and the page declares its language."""
    page, _, _ = open_page(browser, site, viewport={"width": 1200, "height": 900})
    unnamed = page.evaluate("""() => {
      const name = (e) => (e.getAttribute('aria-label') || e.getAttribute('title') || e.textContent
                           || (e.labels && e.labels.length ? e.labels[0].textContent : '')
                           || e.getAttribute('placeholder') || '').trim();
      const visible = (e) => e.offsetParent !== null;
      return [...document.querySelectorAll('button, a[href], input, select, textarea')]
        .filter(visible).filter((e) => e.type !== 'hidden' && !name(e))
        .map((e) => e.outerHTML.slice(0, 80));
    }""")
    assert unnamed == []
    assert page.evaluate("[...document.images].filter((i) => !i.hasAttribute('alt')).length") == 0
    assert page.evaluate("document.documentElement.lang") == "en"
    page.close()


def test_page_code_stays_within_budget(browser, site):
    page, _, sizes = open_page(browser, site, viewport={"width": 1200, "height": 900})
    total = sum(sizes.values())
    assert 50_000 < total < BUDGET_CODE, json.dumps(sizes)
    lazy = page.evaluate("[...document.querySelectorAll('#finds img, #near img')].every((i) => i.loading === 'lazy')")
    assert lazy
    page.close()
