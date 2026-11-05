# Research spike: Babelia's location → image mapping

Plan item 3.2, time-boxed. Question: is the archive's mapping from a location number to an image
a perfect scramble, or does it have structure that could make generation or directed search
possible?

## Method

- **Downloads:** `/imagedl.cgi` returns JPEG too, so exact pixel values aren't available. Babel
  uses 16 levels per channel (0, 17, …, 255), so rounding each decoded pixel to the nearest level
  recovers it. On simulated frames encoded with Babelia's quantisation tables, **82.4%** of values
  come back exactly; the errors are random.
- **Frames:** locations 1, 2, 3, 4 and 6, as 0–15 levels per channel. That's 798,720 values per
  frame.
- **Chance level:** two unrelated frames agree on 1 value in 16 (0.0625). A strict relation would
  show about 0.68, since three noisy recoveries each have 82% accuracy.

## Results

| hypothesis | agreement | verdict |
|---|---|---|
| same pixel values (L1 = L2) | 0.0623 | chance |
| additive: L3 = L1 + L2, L2 = 2·L1, L4 = 2·L2, L6 = L2 + L4 (mod 16) | 0.062–0.063 | chance |
| xor: L3 = L1 ⊕ L2, L6 = L2 ⊕ L4 | 0.063 | chance |
| L2 is a shifted copy of L1 in the value stream (pixel- or channel-major) | max \|z\| 4.8 over 1.6M shifts | chance (null max ≈ 5.5) |
| **constant step: L3 − L2 = L2 − L1 (mod 16)** | **0.103** (R 0.101, G 0.114, B 0.095) | **far above chance** |
| constant step at spacing 2: L6 − L4 = L4 − L2 | **0.102** | same effect |
| doubled step: L4 − L2 = 2(L2 − L1) | 0.075–0.085 | weaker |

**Controls:** the same constant-step test gives 0.0624 on three independent simulated frames and
0.0627 on three unrelated real frames. The effect isn't a JPEG artefact.

**Second-difference distribution** (L3 − 2·L2 + L1 mod 16, relative to chance): 0 → **1.65×**,
±1 → **1.38×**, smoothly falling to about 0.75× at ±5–6. It's identical for spacings 1 and 2.
**First differences are perfectly uniform.**

## Interpretation

- Consecutive locations are **not** independent. Pixel-value correlation (−0.0002, used in the
  first version of the search plan) is blind to this: the relation is in modular differences, not
  values.
- A second difference concentrated at 0 and ±1, with uniform first differences, is the signature
  of a partly **affine big-number map with carries**: something like value = A·L + B in a large
  integer, read out in digits, where neighbouring digits leak carries. That's consistent with the
  156-row cross-channel fingerprint, which also suggests digit-level structure.
- The effect is spread evenly over all rows, columns and channels, and is only partial. Allowing
  for 18% recovery noise, roughly 10–15% of values behave affinely between equally spaced
  locations. So the mapping isn't a plain multiply either; there's an extra mixing step.

## What it means for the scanner

- Detection is unaffected: each frame on its own still behaves like static.
- Scanning consecutive locations samples very slightly related frames. For finding anomalies this
  is negligible. Random starts each run, as the scanner already does, avoid any systematic effect.
- **Recovering the mapping looks plausible but not quick.** Next steps, if pursued:
  1. Fetch L, L+1, …, L+31 for a few starting points and fit, per value position, an affine model
     with carry terms to the recovered levels.
  2. Use the seeded demos (known images at known ~961,760-digit locations) as exact test cases
     for any candidate formula.
  3. If a formula reproduces frames at over 80% agreement (the recovery ceiling), local generation
     (plan item 1.6) becomes possible.

Status: **promising lead, not resolved.** Time box used.
