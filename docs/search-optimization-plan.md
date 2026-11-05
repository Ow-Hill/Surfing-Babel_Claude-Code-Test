# Search optimisation plan

## Where we are (measured 2026-11-05)

| fact | value | consequence |
|---|---|---|
| Babelia fetch time | 5–32 s per frame, almost all server-side render (a TLS handshake is 0.35 s) | the server is the bottleneck |
| Our scoring | 0.6 s per frame (+0.4 s analysis, only for kept frames) | not a bottleneck at current rates |
| Throughput | ~31 frames/min with 3 workers (~1,900/hour) | ~45,000 frames/day if scanning nonstop |
| Locality | frames at N and N+1 share no structure (pixel correlation −0.0002) | Babelia scrambles locations, so no search can "move towards" a picture |
| Find rate | ~1 in 1,700–2,100 frames at 4σ, all from the colour-balance test so far | anomalies are statistical, not pictures |
| Odds of a picture | ~4096^−266,240 per frame | no throughput gain changes this: random search won't find a picture |

The scanner can be made better at what it's actually doing: measuring Babel's static and
collecting its strangest frames. "Optimising the search" has three meanings, and the plan
covers each:

1. **More frames per hour** (throughput).
2. **Better use of each frame** (detection quality, calibration and what gets kept).
3. **Searching smarter than random** (directed search). This is mostly impossible, but there is
   one real research lead.

## 1. Throughput

| # | Idea | Gain | Effort | Risk / notes |
|---|---|---|---|---|
| 1.1 | **Adaptive concurrency.** Measure Babelia's response time and add or remove workers (2–6) to keep latency near its unloaded level. Back off on errors. | 1–2× | S | Babelia is a one-person site. Never push latency up. |
| 1.2 | **Persistent HTTPS connections** (`http.client`, one per worker). | <5% | S | TLS is 0.35 s of a 5–30 s fetch; tidy but minor. |
| 1.3 | **Scan more often.** Hourly 50-minute runs instead of 25 minutes every 3 h. Actions minutes are free for public repos. | ~6× frames/day | S | Same politeness concern as 1.1. Combine with it. |
| 1.4 | **Parallel runners** (matrix of N jobs, distinct start locations, results merged at publish). | N× | M | Multiplies load on Babelia. Only with explicit rate caps. |
| 1.5 | **Publish incrementally** during long batches (every 30 min). | results sooner; nothing lost if a run dies | M | Needs a merge-safe publish (rebase data, not force-push). |
| 1.6 | **Reproduce Babelia's generator locally** (see 3.2). | 1,000×+ (CPU-bound, ~0.6 s/frame/core) | L–XL | Research. Only possible if the location→image mapping can be recovered. |

**Recommended:** 1.1 + 1.3 together (about 6–10× more frames, still polite), then 1.5.

## 2. Better use of each frame

| # | Idea | Gain | Effort | Notes |
|---|---|---|---|---|
| 2.1 | **Log every frame's ten test scores** (≈80 bytes/frame; ~3.6 MB/day at 45k frames). | enables everything below | S | Store as a compact binary or CSV.gz on gh-pages. Today only a histogram is kept. |
| 2.2 | **Per-test thresholds from real data.** Calibrate each test so it has the same false-positive rate on real frames. | finds from all tests, not only colour balance | S (after 2.1) | Fixes the "all finds are tone" skew honestly. |
| 2.3 | **Combined score** (Stouffer/Fisher over tests). | catches weak structure spread across several tests | S | Report alongside max-σ. |
| 2.4 | **Two-stage cascade:** cheap tests first (~60 ms), expensive ones (angled, repetition, symmetry: ~500 ms) only when needed. | 5–8× less CPU | M | Matters only if 1.6 succeeds. Must keep calibration exact. |
| 2.5 | **Learned detector:** a small CNN trained on unlimited synthetic "picture in static" data, calibrated on real static. | catches shapes no hand-written test targets | M–L | Keep the statistical tests as the source of σ; the CNN is an extra test with its own calibration. |
| 2.6 | **Per-test leaderboards:** keep the top N frames for *each* test, not just overall. | more varied "closest calls" | S | Cheap and good for the site. |
| 2.7 | **Fingerprint monitor:** track the 156-row red→green / green→blue correlation per frame. | detects if Babelia's generator changes | S | Also feeds research 3.2. |

**Recommended:** 2.1 → 2.2 → 2.6 (a weekend's work, visible on the site immediately).

## 3. Smarter than random

| # | Idea | Verdict |
|---|---|---|
| 3.1 | Hill-climbing or "nearby" search in location space | **Not possible.** Neighbouring locations are unrelated (measured). |
| 3.2 | **Recover Babelia's location→image mapping.** The 156-row cross-channel fingerprint shows it isn't a perfect scramble. With (location, image) pairs, including the 961,760-digit demo locations, test hypotheses: base-4096 digits under a modular multiplication or permutation, channel interleaving at a stride of 1/8 of the values, LCG-style structure. | **The one real lead.** If recovered: local generation (1.6) and true directed search, i.e. computing the location of any image, or scanning structured neighbourhoods. High effort, uncertain. |
| 3.3 | Search outward from known pictures (seeded demos) | Equivalent to 3.2: without the mapping, a picture's neighbours are random static. |
| 3.4 | Bias the slideshow start range | No effect: every location is equally random. |

## Suggested order

1. **2.1** log all scores, then **2.2** per-test thresholds and **2.6** per-test leaderboards: quick, improves the site.
2. **1.1 + 1.3** adaptive politeness and more frequent scans: about 6–10× more frames.
3. **1.5** incremental publishing.
4. **3.2** fingerprint research spike (time-boxed: one day). Go or no-go on **1.6**.
5. **2.5** learned detector, if the site wants richer finds.
