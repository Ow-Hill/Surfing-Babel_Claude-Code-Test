# Surfing Babel

**Live site: https://ow-hill.github.io/Surfing-Babel_Claude-Code-Test/**

The [Babel Image Archive](https://babelia.libraryofbabel.info/slideshow.html) contains every
possible 640×416 image, so nearly all of it is random static. Surfing Babel pulls frames from the
archive's slideshow, scores how far each one is from pure static and keeps anything that looks
like *something*, along with its location (the seed that regenerates it).

## How it works

```
GitHub Actions (hourly) ─► scan.py ─► POST babelia.cgi location=N, N+1, … ─► classifier
                                                                                    │
                    gh-pages branch ◄─ build_site.py ◄─ data/*.json + images ◄──────┘
                         │
                         └─► GitHub Pages
```

* **Scanner** (`babel_surfer/scan.py`) does what the slideshow's own `/js/slideshow.js` does: it
  picks a random location below 9,007,199,250,000,000, then requests `location=N, N+1, …` from
  `/babelia.cgi`. Each frame is a 640×416 JPEG, and the location number *is* its seed
  (`https://babelia.libraryofbabel.info/babelia.cgi?location=N` shows it again). Babelia takes
  5–30 s to render a frame, so concurrency is **adaptive**: it starts at 3 parallel requests,
  opens up to 5 while response time stays near its best, and backs off (down to 1) as soon as
  responses slow or fail.
* **Classifier** (`babel_surfer/classifier.py`): ten statistical tests, each run on brightness and both colour
  channels, each expressed in **σ** (standard deviations away from what static produces):

  | test | catches |
  |---|---|
  | shape | regions, gradients, big shapes (variance of 4–64 px block means) |
  | blob | small strong objects (most extreme sliding 8–48 px window) |
  | lines | horizons, stripes, bands (rows/columns, 1–8 px thick) |
  | texture | smooth surfaces or fine checkerboards (neighbour correlation) |
  | palette | greyscale or limited palettes (cross-channel correlation) |
  | tone | too dark, too bright, too few colours (histogram vs. static's) |
  | contrast | patches flatter or noisier than static, even ones whose average colour blends in (8–32 px local variance) |
  | angled | lines and bands at 15° steps between rows and columns |
  | repetition | any part of the frame recurring at an offset: tiles, repeated motifs (2-D autocorrelation) |
  | symmetry | mirror symmetry about any vertical or horizontal axis |

* **Calibration** (`babel_surfer/calibrate.py`): every test is re-centred on its null
  distribution, measured on simulated Babel static. The simulation uses 16 levels per channel
  and is JPEG-encoded with **Babelia's own quantization tables**, read from real frames. The
  tone baseline comes from 48 real Babelia frames (leave-one-out). With Pillow's generic
  quality-90 encoder instead, real static scored up to 14σ on the tone test.

## Babelia's fingerprint

Real frames are not perfectly random. In every frame, **red at row y correlates with green at row
y + 156, and green with blue** (about +10σ per frame, r ≈ 0.025). 156 rows is exactly 1/8 of the
frame's 798,720 colour values. The correlation is the same in every frame tested (147 real
frames), so it comes from the archive's generator, not from any one image. The repetition test
ignores that one offset; otherwise every frame would look like an anomaly.

## Threshold for interesting images

| score | tier | what happens |
|---|---|---|
| < 3σ | static | counted |
| 3–4σ | near miss | top 12 kept as "closest calls" |
| **≥ 4σ** | **anomaly** (4–8), **faint** (8–16), **clear** (16–40), **striking** (≥ 40) | **saved as a find** with seed and image |

The find threshold is **4σ**. Finds between 4σ and 8σ are **anomalies**: the strangest static, not
pictures. **8σ** is the line for a genuine pattern; the benchmark shows shapes far too faint to see
still scoring well above it. Change the threshold with `scan --threshold`.

**Honest note on the rate.** The first version of the colour-balance test converted a
chi-square statistic to σ with the naive (χ² − df)/√(2·df), which ignores the skew and overstated
the upper tail. All 12 early finds came from that test. It now uses the Wilson–Hilferty
transform (as do the variance-based tests), and after re-scoring none of the 12 still reaches 4σ.
With the corrected scale, real static maxed at 2.8σ over 147 frames and simulated static at 3.8σ
over 1,000. Every frame's scores are now logged (`data/scores.npy`, `stats.test_hist`), so the true
4σ rate per test is measured from real scans; the leaderboards show the top frames per test in
the meantime.

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

## Proof it works (`python -m babel_surfer.seeded`)

Random frames essentially never contain a picture, so the pipeline is also run backwards:
1. Draw a picture.
2. Upload it to Babelia's image search (`/imagesearch.cgi`), which returns its location in the
   archive.
3. Fetch that location back with the scanner's own code and score it.

| seeded demo | location size | score |
|---|---|---|
| drawn landscape | 961,760 digits | 339,434σ (striking) |
| red disc blended into static at 5% | 961,760 digits | 135σ (striking) |

Pictures live at locations around 960,000 digits long, which is why the slideshow (which starts
below 10¹⁶) never reaches them. The demos appear on the site, clearly labelled, never as finds.

## Site features

- **Evidence overlay:** each kept frame stores a small `maps.json`, computed from the full frame at
  scan time, because previews lose static's statistics. The detail view draws it as five layers:
  brightness and colour, contrast, rows and columns, lines/repeats/symmetry, and colour balance
  (a mask of the pixels in the most deviant colour range, plus a histogram chart). Each layer has
  a threshold slider. The default threshold is where static rarely reaches, lowered per frame
  when needed so the frame's strongest evidence is always visible.
- **Descriptions:** every find, near miss and demo gets one or two sentences generated from its
  maps, for example "a darker spot around the centre, roughly 40 px across" or "slightly more very
  dark red values (0–15) than static produces". They appear in the detail view, as tile
  captions and in the feed. Finds saved before this existed are backfilled at the start of the next scan, by
  re-fetching their full frames from Babelia.
- **Share links:** `…/#<location>` opens a find. `#demo-landscape` opens a demo. Any other number
  opens that location straight from Babelia, unscored.
- **Live from Babelia:** frames stream from the archive in your browser, walking locations like
  its slideshow. They're shown, not scored: Babelia sends no CORS headers, so a page can display
  its images but not read their pixels.
- **Alerts:** `feed.xml` is an Atom/RSS feed of finds; subscribe in any feed reader. If a scan
  finds anything at **≥ 8σ**, the workflow opens a GitHub issue, which emails repository
  watchers. The ~8 daily 4–8σ anomalies go to the feed only, to avoid spam.

## Per-test leaderboards, combined score and fingerprint monitor

- **Leaderboards:** the top 3 frames for each of the ten tests and for the combined score, with
  each test's measured 4σ rate on real frames.
- **Combined score:** a Stouffer sum of the ten calibrated tests, calibrated separately, which
  catches weak evidence spread across several tests.
- **Fingerprint monitor:** the strength of Babelia's 156-row red→green / green→blue correlation
  in every frame (about +14 so far; simulated static is about 0).
- **Research:** a time-boxed spike on Babelia's generator found partly affine structure between
  equally spaced locations; see [`docs/research-babelia-generator.md`](docs/research-babelia-generator.md).

## Showcase and live demo

`agent.html` on the site tells how the project was built by an AI coding agent: a timeline of
every pull request, including the mistakes the agent caught in its own work. It also shows live
repository activity (pull requests, commits, scans and deploys; `?live=1` refreshes every 15 s)
and a **live-coding slot**: `babel_surfer/demo_slot.py` holds an unimplemented
`find_of_the_day()` whose docstring and tests (`tests/test_demo_slot.py`, skipped until it is
implemented) are the spec. In a demo, Claude Code implements it, the PR merges and the page
updates by itself. See [`docs/live-demo.md`](docs/live-demo.md) for the runbook, and
[`docs/search-optimization-plan.md`](docs/search-optimization-plan.md) for how the search could be
made faster and smarter.

## Usability

The UI was reviewed against Nielsen's 10 usability heuristics; see
[`docs/usability-review.md`](docs/usability-review.md) for every finding, its severity and what
changed.

## Hosting

The workflow `.github/workflows/scan-and-publish.yml` runs **hourly** (45-minute scans), on
pushes that change the code (1-minute scan, so changes go live in a few minutes) and on demand.
It adds results to the data already on the `gh-pages` branch, rebuilds the site and pushes it,
**every 30 minutes** during long scans, so results appear sooner and a failed run loses at most
one chunk. GitHub Pages serves that branch.

Frames scored by an older detector version are re-fetched and re-scored automatically at the start
of the next scan. Each saved frame records its `detector` id.

**Keep-alive:** GitHub turns off scheduled workflows in public repos after 60 days without
activity. The last step of every run re-enables the workflow through GitHub's API, which resets
that timer.

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
