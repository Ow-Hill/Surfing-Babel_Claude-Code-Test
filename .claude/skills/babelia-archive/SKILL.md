---
name: babelia-archive
description: Facts about the Babel Image Archive (babelia.libraryofbabel.info) that shape the scanner. Covers the request protocol, frame format, latency and politeness limits, the 156-row fingerprint, and what can and can't speed up the search. Use before fetching from Babelia, changing scan.py's fetching or concurrency, simulating static, or when asked to search faster or smarter.
---

# The Babel Image Archive (Babelia)

## Must
- **Fetch a frame** with `POST https://babelia.libraryofbabel.info/babelia.cgi` and form
  field `location=N`. It returns a 640×416 JPEG. The location *is* the seed.
  `GET babelia.cgi?location=N` returns the same image.
- **Simulate static exactly as Babelia makes it:**
  - 16 levels per channel (0, 17, …, 255), each pixel independent and uniform;
  - JPEG at about q90, 4:4:4, with **Babelia's own quantisation tables**
    (`calibrate.babel_static` / `BABELIA_QTABLES`). Pillow's generic q90 made real static
    score 14σ.
- **Stay polite.** It is a one-person site. Use `scan.Politeness`: start at 3 concurrent
  requests, go up to 8 while latency stays near its best (never more than 40 started in any
  minute: `--max-rate`), back off on errors or when
  responses slow. Identify the client as `SurfingBabel/1.0`, never with personal details.
- **Judge each concurrency level on its own requests, failures included.** Babelia's
  errors come in bursts: every in-flight request fails together. Counted one by one, a
  single burst took 5 → 1 within seconds, several times a chunk on 2026-11-09 (13–18 errors
  per chunk). Requests sent before the last change of level are now ignored, so a burst
  steps down once. Each run records `error_kinds` (timeout, HTTP status, …).
- **Judge each concurrency level on its own requests.** Clear the latency window after every
  change, and let the baseline relax (`DRIFT`). Server latency drifts between about 4 s and
  14 s over a day. Deciding on stale samples against an all-time-best baseline cascaded
  5 → 1 in a minute, many times per chunk.

- **Keep request slots busy, not more of them.** Scoring a frame takes about 0.8 s. When each
  of 5 threads fetched and then scored, its slot sat idle for that time (about 8% at 9–12 s
  latency). `scan()` now runs more threads than slots: `Politeness` still caps requests in
  flight, and the extra threads only score. Each run records `latency_median_s`,
  `latency_p90_s` and `in_flight` (average requests in flight). Judge throughput by those,
  not by `baseline_latency_s`, which is the best rolling median, not the typical latency.

## Never
- Expect a faster endpoint or caching to help. Every request is rendered server-side in
  5–30 s, the same location is re-rendered each time, and `imagedl.cgi` and GET are just as
  slow.
- Add parallel runners, or raise the cap past 8 at once or 40 requests a minute (agreed with
  the owner on 2026-11-10), without the owner's agreement. Scanning *longer* (the serial
  relay) at the same cap is fine. Each run records latency per level
  (`concurrency.by_level`): if the median rises with the level, Babelia is feeling it, so
  lower the cap.
- Treat nearby locations as nearby pictures. Hill-climbing and biased start ranges don't
  work.
- Try to read Babelia pixels in the browser. There is no CORS, so all scoring happens in
  Actions.

## Key facts
- **Real pictures** live at locations about 961,760 digits long (found via `/imagesearch.cgi`).
  Random search will never hit one; finds are statistical anomalies.
- **Fingerprint:** in every real frame, red at row y correlates with green at row y+156, and
  green with blue (about +14σ). `classifier.fingerprint_z` measures it, and the repetition
  test excludes that offset. A frame without the fingerprint would itself be news.
- **Babelia's map is affine in location, and one block is exactly additive.**
  image(L+k) = image(L) + k·A, read as a big number: a row runs from least to most significant
  left to right, blue < green < red within a pixel. In the block y ≥ 380, x ≥ 480 this predicts
  frame L+2 from L and L+1 at the JPEG recovery ceiling (0.57 vs 0.587). Elsewhere a mixing step
  hides it. See `docs/research-babelia-generator.md`. Recovering that mixing is the only route
  to local generation, which would make search CPU-bound and put no load on Babelia.

## Reference
[reference.md](reference.md) covers:
- the slideshow's start range;
- level recovery from JPEG;
- throughput figures;
- the research spike's numbers and next steps.
