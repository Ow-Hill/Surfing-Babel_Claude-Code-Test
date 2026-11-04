# Surfing Babel

A small website that scans the [Babelia slideshow](https://babelia.libraryofbabel.info/slideshow.html)
(the Library of Babel's image archive) for the rare frame that is **more than random static**.
When it finds one, it saves the **seed** and the **image**, and shows them in a gallery.

![flow](https://img.shields.io/badge/slideshow-%E2%86%92%20classifier%20%E2%86%92%20gallery-8a5a2b)

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium     # skip if you already have a Chromium Playwright can use
python app.py                   # open http://localhost:5000
```

Scanning starts as soon as the app starts (set `AUTOSTART=0` to start it from the page instead).

## How it works

```
headless Chromium ──► slideshow page ──► grab frame + seed ──► classifier ──► score ≥ threshold?
                                                                                  │ yes
                                                                 SQLite + data/images/ ──► web gallery
```

1. **Scraper** (`babel_surfer/scraper.py`) opens the slideshow in headless Chromium with
   Playwright and, every `INTERVAL` seconds, reads what is shown:
   - the **image**: the largest visible `<img>` or `<canvas>` (full-resolution download for
     `<img>`, `toDataURL` for `<canvas>`, element screenshot as a last resort);
   - the **seed**: from `SEED_SELECTOR` if set, otherwise heuristically from elements, inputs or
     links mentioning *seed / location / bookmark*, then from `location=` / `seed=` query
     parameters in the image or page URL.

   Frames are de-duplicated by hash, so polling faster than the slideshow is harmless.
2. **Classifier** (`babel_surfer/classifier.py`) decides static vs. "something". Babel images are
   independent random pixels, so it measures departure from that:
   - **multi-scale block-variance ratio**: average the image over k×k blocks. For i.i.d. noise
     the variance of block means is exactly σ²/k², so the ratio *observed / expected* is ≈ 1
     for static and grows fast for any shape, gradient or region, even a faint one;
   - **neighbour-pixel correlation**: ≈ 0 for static, high for real pictures;
   - **compressibility**: static doesn't compress.

   On synthetic tests, 200 random-static frames never scored above 1.36, while a circle only
   +10 brightness above the noise (barely visible) scored 5.8. Default threshold: **3.0**.
   Labels: `static` < threshold ≤ `faint structure` < 3×threshold ≤ `structure`.
3. **Storage** (`babel_surfer/storage.py`): finds go in `data/babel.db` (SQLite) with the image
   in `data/images/`. Duplicates are skipped by hash.
4. **Website** (`app.py`, `templates/`, `static/`): live scanner status, a sparkline of recent
   scores, and a gallery of finds. Click one to see the full image, copy the seed, download it,
   or delete it. The threshold can be changed live.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `SLIDESHOW_URL` | Babelia slideshow URL | Page to scan |
| `IMAGE_SELECTOR` | auto (largest img/canvas) | CSS selector for the slide image |
| `SEED_SELECTOR` | auto (heuristic) | CSS selector for the element holding the seed |
| `NEXT_SELECTOR` | none (slideshow auto-advances) | Button to click to advance |
| `INTERVAL` | `1.0` | Seconds between frame checks |
| `THRESHOLD` | `3.0` | Minimum score to save a frame |
| `CHROMIUM_PATH` | auto | Path to a Chromium binary |
| `DATA_DIR` | `./data` | Where the DB and images go |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Web server bind |
| `AUTOSTART` | `1` | Start scanning on launch |

If the seed shown in the gallery isn't the right value, open the slideshow in your browser,
inspect the element showing the seed, and set `SEED_SELECTOR` to it (likewise for the image).

## API

- `GET /api/finds`, `GET/DELETE /api/finds/<id>`, `GET /images/<file>`
- `GET /api/status`, `POST /api/scraper/start`, `POST /api/scraper/stop`
- `POST /api/threshold` `{"threshold": 4}`
- `POST /api/classify` (multipart `image`): score any image, handy for checking the classifier

## Tests

```bash
python -m pytest -q
```

Includes an end-to-end test that runs the real scraper against a local stand-in slideshow
(`tests/fixtures/slideshow.html`), which alternates static with frames containing a shape and
checks that only the shapes get saved, with the right seeds.
