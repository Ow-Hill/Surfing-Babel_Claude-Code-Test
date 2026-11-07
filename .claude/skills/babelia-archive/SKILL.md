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
  requests, go up to 5 while latency stays near its best, back off on errors or when
  responses slow. Identify the client as `SurfingBabel/1.0`, never with personal details.

## Never
- Expect a faster endpoint or caching to help. Every request is rendered server-side in
  5–30 s, the same location is re-rendered each time, and `imagedl.cgi` and GET are just as
  slow.
- Add parallel runners or more workers without the owner's agreement and a hard rate cap.
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
- **Equally spaced locations share partly affine structure.** Recovering Babelia's mapping is
  the only route to directed search or local generation. It remains an open research lead.

## Reference
[reference.md](reference.md) covers:
- the slideshow's start range;
- level recovery from JPEG;
- throughput figures;
- the research spike's numbers and next steps.
