---
name: babelia-archive
description: Facts about the Babel Image Archive (babelia.libraryofbabel.info) learned while building Surfing Babel - the request protocol, frame format, latency and politeness, its 156-row fingerprint, the partial affine structure between locations, and what does not speed up the search. Use before fetching from Babelia, changing scan.py's fetching or concurrency, or researching directed search.
---

# The Babel Image Archive (Babelia)

## Protocol
- **Fetching a frame:** `POST https://babelia.libraryofbabel.info/babelia.cgi` with
  `location=N` (form-encoded) returns a 640×416 JPEG (~460 KB). `GET babelia.cgi?location=N`
  and `GET imagedl.cgi?location=N` return the same frame.
- **The location is the seed.** The slideshow (`/slideshow.html`, `/js/slideshow.js`) picks
  a random start below 9,007,199,250,000,000 and requests N, N+1, …
- **Real pictures** have locations of about 961,760 digits, found by uploading the image to
  `/imagesearch.cgi`. `babel_surfer/seeded.py` keeps two such demos (a landscape and a faint
  disc) as proof that the detector works.
- **Frame format:** every pixel is an independent uniform 12-bit colour (16 levels per channel:
  0, 17, …, 255), JPEG-encoded at about quality 90, 4:4:4, with Babelia's own quantisation
  tables (`calibrate.BABELIA_QTABLES`).
  - Simulate static only with those tables: Pillow's generic q90 made real static score
    14σ on the tone test.
  - Rounding decoded pixels to the nearest level recovers ~82% of values exactly
    (`perceptual.levels`).
- **No CORS:** browsers can't read Babelia pixels, so all scoring happens in GitHub Actions
  and the site only displays frames.

## Latency and politeness
- Every request is rendered server-side in 5–30 s (10–17 s typical). A TLS handshake is
  only 0.35 s. All three endpoints are equally slow, and **the same location is re-rendered
  every time** (no cache). No endpoint or keep-alive trick is faster.
- **It is a one-person site.** `scan.Politeness` starts at 3 concurrent requests, rises to 5
  while the median latency stays below 1.3× its best, backs off above 2× or on an error, and
  never drops below 1.
- **Throughput:** about 400–700 frames per 45-minute run, roughly 12–15 frames/min.
- **Don't add parallel runners or more workers** without an explicit rate cap and the
  owner's agreement.
- **User-Agent:** `SurfingBabel/1.0 (+repo URL)`. Never put the owner's email in requests.

## Structure found in "random" frames
- **The 156-row fingerprint:** red at row y correlates with green at y+156, and green with
  blue. That gives about +14σ in every real frame and ~0 in simulated static.
  - `classifier.fingerprint_z` tracks it; `stats.fingerprint` monitors it. A frame without it
    would itself be a discovery.
  - The repetition test excludes that offset.
- **A research spike** (`docs/research-babelia-generator.md`) found that equally spaced
  locations share partly affine structure. Second differences mod 16 cluster at 0 (1.65× chance) and
  ±1 (1.38×), while first differences are uniform. This points to a big-number affine map
  with carries plus mixing; roughly 10–15% of values behave affinely.
- **Recovering that mapping is the only route to real directed search** (generating frames
  locally, or computing the location of an image). It is promising but unresolved; the next
  step is fitting L…L+31 per value position.
- **Things that don't work:** nearby locations are not "closer" to pictures, so
  hill-climbing and biased start ranges are useless.
