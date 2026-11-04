# Surfing Babel

**Live site: https://ow-hill.github.io/Surfing-Babel_Claude-Code-Test/**

The [Babel Image Archive](https://babelia.libraryofbabel.info/slideshow.html) contains every
possible 640×416 image, so nearly all of it is random static. Surfing Babel pulls frames from the
archive's slideshow, scores how far each one is from pure static and keeps anything that looks
like *something*, along with its location (the seed that regenerates it).

## How it works

```
GitHub Actions (every 3 h) ─► scan.py ─► POST babelia.cgi location=N, N+1, … ─► classifier
                                                                                    │
                    gh-pages branch ◄─ build_site.py ◄─ data/*.json + images ◄──────┘
                         │
                         └─► GitHub Pages
```

* **Scanner** (`babel_surfer/scan.py`) does what the slideshow's own `/js/slideshow.js` does: it
  picks a random location below 9,007,199,250,000,000, then requests `location=N, N+1, …` from
  `/babelia.cgi`. Each frame is a 640×416 JPEG, and the location number *is* its seed
  (`https://babelia.libraryofbabel.info/babelia.cgi?location=N` shows it again). It uses 3
  parallel requests to stay polite: the server takes 5 to 25 s per frame.
* **Classifier** (`babel_surfer/classifier.py`): six statistical tests, each run on brightness and both colour
  channels, each expressed in **σ** (standard deviations away from what static produces):

  | test | catches |
  |---|---|
  | shape | regions, gradients, big shapes (variance of 4–64 px block means) |
  | blob | small strong objects (most extreme sliding 8–48 px window) |
  | lines | horizons, stripes, bands (rows/columns, 1–8 px thick) |
  | texture | smooth surfaces or fine checkerboards (neighbour correlation) |
  | palette | greyscale or limited palettes (cross-channel correlation) |
  | tone | too dark, too bright, too few colours (histogram vs. static's) |

* **Calibration** (`babel_surfer/calibrate.py`): every test is re-centred on its null
  distribution, measured on simulated Babel static. The simulation uses 16 levels per channel
  and is JPEG-encoded with **Babelia's own quantization tables**, read from real frames. The
  tone baseline comes from 48 real Babelia frames (leave-one-out). With Pillow's generic
  quality-90 encoder instead, real static scored up to 14σ on the tone test.

## Threshold for interesting images

| score | tier | what happens |
|---|---|---|
| < 3σ | static | counted |
| 3–4σ | near miss | top 12 kept as "closest calls" |
| **≥ 4σ** | **anomaly** (4–8), **faint** (8–16), **clear** (16–40), **striking** (≥ 40) | **saved as a find** with seed and image |

The find threshold is **4σ**, chosen so the scanner actually finds things. In a 4,000-frame
simulation of Babel static, about 1 in 400 frames reached 4σ, so expect 2–3 finds per hour of
scanning (about 1 per scheduled run). These **anomalies** are the strangest static, not pictures.

**8σ** is the line for a genuine pattern. Pure static never got there: the maximum was 5.45σ over
4,000 simulated frames and 2.88σ over 48 real Babelia frames. The benchmark shows shapes far too
faint to see still scoring well above it. Change the threshold with `scan --threshold`.

## Benchmark (`python -m babel_surfer.benchmark`)

Known pictures blended into simulated Babel static (8 trials each):

| hidden signal | strength | detected | weakest score |
|---|---|---|---|
| large disc | 5% | 8/8 | 105σ |
| landscape | 5% | 8/8 | 2,597σ |
| word "BABEL" | 5% | 8/8 | 4,287σ |
| 2 px horizon line | 25% | 8/8 | 16.9σ |
| 24 px square | 25% | 8/8 | 10.1σ |
| greyscale / dark static, 1 px checkerboard | – | 8/8 | > 5,000σ |
| *anything at 2%* | 2% | 0/8 | erased by Babel's 16-level colour quantization |
| pure static (1,000 frames) | – | none reach 8σ | max 4.99σ |

## Hosting

The workflow `.github/workflows/scan-and-publish.yml` runs every 3 hours (and on pushes that
change the code). It scans for 25 minutes, adds the results to the data already on the
`gh-pages` branch, rebuilds the site and pushes it. GitHub Pages serves that branch.

**Storage stays bounded:**
- Only the **top 100 finds** by score are kept. The total count is still tracked.
- Images are stored as **320×208 previews** of about 28 KB, instead of ~460 KB frames. The site
  loads the full frame from Babelia using the find's location (`babelia.cgi?location=N`).
- `gh-pages` is published as one fresh commit each run, so dropped images don't pile up in git
  history.

The published site stays at a few MB, far under Pages' 1 GB limit.

**Scan now button:** the site shows whether a scan is running, using GitHub's public API, which needs no
login. Starting a scan needs a fine-grained token limited to this repository, with **Actions: Read
and write**. You paste it once in the dialog; it is kept only in that browser's localStorage and
sent only to `api.github.com`. Without a token, the dialog links to the workflow's "Run workflow"
page. The site can't scan from your browser because Babelia sends no CORS headers, so browsers
can't read its pixels.

The scheduled run only fires once the workflow is on the default branch (`main`). Until then, run
it from the Actions tab or push to this branch.

## Running locally

```bash
pip install -r requirements.txt
python -m babel_surfer.scan --out public/data --minutes 10
python -m babel_surfer.build_site --out public
python -m http.server -d public 8000
```

Other commands: `python -m babel_surfer.calibrate --real-dir <dir of real frames>` and
`python -m pytest -q`.

## Honest expectations

There are 4096^266,240 possible images, and the slideshow walks through them one at a time.
The chance that a random frame contains a recognisable picture is effectively zero, so expect
the Finds section to stay empty. The histogram and the "closest calls" show that every frame is
still being checked.
