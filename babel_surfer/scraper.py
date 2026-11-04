"""Drive the Babelia slideshow in a headless browser and harvest frames.

The slideshow is a JavaScript page, so rather than hard-coding guesses about
its internal endpoints we load it in Chromium (via Playwright) and read what
it actually renders:

* **Image** - the largest visible ``<img>`` or ``<canvas>`` on the page.
  ``<img>`` sources are downloaded at full resolution, ``<canvas>`` elements
  are exported with ``toDataURL``; if neither works the element is
  screenshotted.
* **Seed** - read from ``SEED_SELECTOR`` if configured, otherwise found
  heuristically: elements / inputs / links whose id, class, name or href
  mention seed / location / bookmark, then any ``location=`` / ``seed=``
  query parameter in the image URL or page URL.
* **Advancing** - the slideshow advances by itself; if ``NEXT_SELECTOR`` is
  set the scraper clicks it instead. Frames are de-duplicated by hash, so
  polling faster than the slideshow is harmless.

Everything site-specific is overridable with environment variables (see
README) in case the page's markup differs from what the heuristics expect.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from .classifier import DEFAULT_THRESHOLD, classify
from .storage import Store

log = logging.getLogger(__name__)

SLIDESHOW_URL = os.environ.get(
    "SLIDESHOW_URL", "https://babelia.libraryofbabel.info/slideshow.html")
IMAGE_SELECTOR = os.environ.get("IMAGE_SELECTOR")   # e.g. "#slide img"
SEED_SELECTOR = os.environ.get("SEED_SELECTOR")     # e.g. "#location"
NEXT_SELECTOR = os.environ.get("NEXT_SELECTOR")     # e.g. "#next"
CHROMIUM_PATH = os.environ.get("CHROMIUM_PATH")

SEED_PARAMS = ("location", "seed", "loc", "id", "hex", "image")

# Runs in the page. Returns the chosen image element's details + seed hints.
_PAGE_PROBE = r"""
([imageSel, seedSel]) => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 16 && r.height > 16 && s.visibility !== 'hidden'
           && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05;
  };
  let candidates = imageSel
    ? Array.from(document.querySelectorAll(imageSel))
    : Array.from(document.querySelectorAll('img, canvas'));
  candidates = candidates.filter(visible);
  candidates.sort((a, b) => {
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
    return rb.width * rb.height - ra.width * ra.height;
  });
  const el = candidates[0] || null;
  let image = null;
  if (el) {
    document.querySelectorAll('[data-babel-target]').forEach(
      e => e.removeAttribute('data-babel-target'));
    el.setAttribute('data-babel-target', '1');
    image = { tag: el.tagName.toLowerCase(),
              src: el.currentSrc || el.src || null,
              alt: el.alt || el.title || null };
    if (image.tag === 'canvas') {
      try { image.dataUrl = el.toDataURL('image/png'); } catch (e) {}
    }
  }

  const textOf = e => (e.value || e.getAttribute('href') ||
                       e.textContent || '').trim();
  const hints = [];
  if (seedSel) {
    document.querySelectorAll(seedSel).forEach(e => hints.push(textOf(e)));
  } else {
    const re = /seed|location|bookmark|\bloc\b|coord/i;
    document.querySelectorAll('input, textarea, a, span, div, p, td, code, pre')
      .forEach(e => {
        const attrs = [e.id, e.className && e.className.baseVal === undefined
                       ? e.className : '', e.getAttribute('name') || '',
                       e.getAttribute('href') || ''].join(' ');
        if (re.test(attrs) && e.children.length <= 2) {
          const t = textOf(e);
          if (t && t.length < 100000) hints.push(t);
        }
      });
  }
  return { image, hints, pageUrl: location.href, title: document.title };
}
"""


# Used if Playwright's own browser download is missing / a different version.
FALLBACK_CHROMIUM = ("/opt/pw-browsers/chromium", "/usr/bin/chromium",
                     "/usr/bin/chromium-browser", "/usr/bin/google-chrome")


def _launch(pw):
    if CHROMIUM_PATH:
        return pw.chromium.launch(headless=True, executable_path=CHROMIUM_PATH)
    try:
        return pw.chromium.launch(headless=True)
    except Exception as e:
        if "Executable doesn't exist" not in str(e):
            raise
        for path in FALLBACK_CHROMIUM:
            if os.path.exists(path):
                log.info("using fallback Chromium at %s", path)
                return pw.chromium.launch(headless=True, executable_path=path,
                                          args=["--no-sandbox"])
        raise RuntimeError("No Chromium found: run `playwright install chromium`"
                           " or set CHROMIUM_PATH") from e


@dataclass
class ScraperState:
    running: bool = False
    status: str = "idle"
    last_error: str | None = None
    last_frame: dict | None = None   # info about the most recent frame
    started_at: float | None = None
    session_scanned: int = 0
    session_found: int = 0
    recent: list = field(default_factory=list)  # last N scores, for the UI


def _seed_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in SEED_PARAMS:
        if qs.get(key):
            return qs[key][0]
    if parsed.fragment and len(parsed.fragment) > 3:
        return parsed.fragment
    return None


def pick_seed(hints: list[str], image_src: str | None,
              page_url: str | None) -> str:
    """Choose the best seed string from everything the page exposed."""
    for h in hints:
        s = _seed_from_url(h) if "=" in h else None
        if s:
            return s
    # longest plain-text hint is most likely the seed/location value itself
    plain = [h for h in hints if h and not h.lower().startswith(("http", "/"))]
    if plain:
        return max(plain, key=len)
    for url in (image_src, page_url):
        s = _seed_from_url(url)
        if s:
            return s
    if image_src and not image_src.startswith("data:"):
        return image_src
    return "unknown"


class Scraper:
    def __init__(self, store: Store, threshold: float = DEFAULT_THRESHOLD,
                 interval: float = 1.0):
        self.store = store
        self.threshold = threshold
        self.interval = interval
        self.state = ScraperState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- control ---------------------------------------------------------
    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self.state = ScraperState(running=True, status="starting",
                                  started_at=time.time())
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self.state.status = "stopping"

    # ---- worker ----------------------------------------------------------
    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._fail("playwright is not installed (pip install playwright)")
            return
        try:
            with sync_playwright() as pw:
                browser = _launch(pw)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                self.state.status = f"loading {SLIDESHOW_URL}"
                page.goto(SLIDESHOW_URL, wait_until="networkidle",
                          timeout=60_000)
                self.state.status = "scanning"
                last_hash = None
                while not self._stop.is_set():
                    try:
                        last_hash = self._tick(page, last_hash)
                        self.state.last_error = None
                    except Exception as e:  # keep scanning on per-frame errors
                        log.exception("frame failed")
                        self.state.last_error = f"{type(e).__name__}: {e}"
                    if NEXT_SELECTOR:
                        try:
                            page.click(NEXT_SELECTOR, timeout=5_000)
                        except Exception as e:
                            self.state.last_error = f"next click: {e}"
                    self._stop.wait(self.interval)
                browser.close()
            self.state.status = "stopped"
        except Exception as e:
            self._fail(f"{type(e).__name__}: {e}")
        finally:
            self.state.running = False

    def _fail(self, msg: str) -> None:
        log.error(msg)
        self.state.last_error = msg
        self.state.status = "error"
        self.state.running = False

    def _grab_image(self, page, image: dict) -> tuple[bytes, str]:
        if (image.get("dataUrl") or "").startswith("data:image"):
            header, b64 = image["dataUrl"].split(",", 1)
            return base64.b64decode(b64), header.split("/")[1].split(";")[0]
        src = image.get("src")
        if src and src.startswith("data:image"):
            header, b64 = src.split(",", 1)
            return base64.b64decode(b64), header.split("/")[1].split(";")[0]
        if src and src.startswith("http"):
            resp = page.context.request.get(src, timeout=30_000)
            if resp.ok:
                ctype = resp.headers.get("content-type", "image/png")
                ext = ctype.split("/")[-1].split(";")[0] or "png"
                return resp.body(), "jpg" if ext == "jpeg" else ext
        # fallback: screenshot exactly the element as shown
        return page.locator("[data-babel-target]").screenshot(type="png"), "png"

    def _tick(self, page, last_hash: str | None) -> str | None:
        probe = page.evaluate(_PAGE_PROBE, [IMAGE_SELECTOR, SEED_SELECTOR])
        image = probe.get("image")
        if not image:
            self.state.status = "scanning (no image on page yet)"
            return last_hash
        data, ext = self._grab_image(page, image)
        digest = hashlib.sha256(data).hexdigest()
        if digest == last_hash:
            return last_hash           # slideshow hasn't advanced yet

        result = classify(data, threshold=self.threshold)
        seed = pick_seed(probe.get("hints", []), image.get("src"),
                         probe.get("pageUrl"))
        self.store.incr("scanned")
        self.state.session_scanned += 1
        self.state.status = "scanning"
        self.state.last_frame = {"seed": seed[:200], "time": time.time(),
                                 **result.to_dict()}
        self.state.recent = (self.state.recent + [result.score])[-60:]

        if result.is_interesting:
            src = image.get("src") or ""
            new_id = self.store.save_find(
                seed=seed, image_bytes=data, ext=ext, score=result.score,
                label=result.label, metrics=result.to_dict(),
                source_url=src if src.startswith("http") else None,
                page_url=probe.get("pageUrl"))
            if new_id:
                self.state.session_found += 1
                log.info("find #%s score=%.2f seed=%s", new_id, result.score,
                         seed[:60])
        return digest
