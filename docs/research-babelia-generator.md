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

## Follow-up (2026-11-10): an exactly additive region

Six frames, fetched one at a time: L = 1,000,000,000 + {0, 1, 2} and L' = 7,345,678,901,234 + {0, 1, 2}.

- **The step is global.** The first difference image(L+1) − image(L) (mod 16) agrees with
  image(L'+1) − image(L') at 0.089 over the whole frame (chance 0.0625), although L' is 7×10¹²
  away. Adding 1 to a location adds the same thing everywhere in location space.
- **The structure is spatially concentrated.** The second-difference rate is about 0.08 in the
  top half and 0.11 in rows 210–370. It is **0.36** in a sharply bounded block: the last ~36 rows
  (y ≥ 380) by the right quarter (x ≥ 480). Columns 480–639 are raised in every row (0.13 vs
  0.07–0.08).
- **Digit order (from carries).** A second difference of ±1 at a value is explained by a change
  of overflow at its less significant neighbour. The correlation is 0.48–0.56 for one neighbour
  and below 0.03 for the rest. The carry into green comes from blue of the same pixel, into red
  from green, and into blue from red of the pixel **to the left**. So a row reads as one
  big number with significance rising left to right, and within a pixel blue < green < red.
- **In the block it is exact addition.** Predicting frame L+2 from L and L+1 by big-number
  addition (step a = d1 − d0 − carry, then d1 + a + carry, carries propagated from the
  prediction itself) gets **0.576 and 0.568** of values right. The ceiling, with three
  JPEG-recovered frames at 82.4% each, is 0.587. On a top-left control patch the same model
  scores 0.11 (chance). In the far pairs, block steps agree within ±1 for 88% of values
  (chance 19%).

**So:** Babelia's map is affine in location (image(L + k) = image(L) + k·A as a big number), and
some further step mixes most of the frame but leaves this high-order block untouched. That is the
foothold for recovering the rest.

**Next:**
1. Fetch L … L+31 at two or three bases, serially (~100 requests, ~20 minutes).
2. Map every region's second-difference rate at full resolution to find the mixing step's
   boundaries. The raised column band and the row bands suggest tiles.
3. Model what the mixing does to an exactly additive number, using the block as ground truth.
4. Any formula must reproduce whole frames at the 82% recovery ceiling. Then the slideshow range
   can be generated locally, at CPU speed and with no load on Babelia.

## Mapping (2026-11-10): the mixing works in fixed tiles

Locations L … L+31 at three bases (10⁹, 7.3×10¹², 4.5×10¹⁵), 90 requests, fetched one at a
time. Per value, the rate of second difference 0 over 30 triples:

- **Fixed tiles.** The map of that rate in 16 px blocks is the same at all three bases
  (correlation 0.997–0.998), so it belongs to the generator, not to the location.
- **Sharp edges.** Columns change at exactly x = 160 and x = 480, so tiles are 160 px wide.
  Rows change at y = 156 (right quarter), 208 (left quarter), 364 and 380 (right quarter).
  156, 208 and 364 are multiples of 52, and 156 is the offset of the cross-channel
  fingerprint.
- **Three levels.**
  - Top tiles, x 160–480 and y < 208: none (0.071 against 0.0625 chance).
  - Most of the frame: weak (0.08–0.11).
  - Right quarter, y 364–380: 0.20.
  - Right quarter, y ≥ 380: exact addition (0.36).
- **Independent of spacing.** Second differences at spacing 1, 2, 3, 4 and 8 give the same
  rates in every region (corner 0.36, middle 0.11, top 0.071). The map is affine in L, with
  no periodic component at these spacings.

**Reading:** a carry-propagating mixing step works tile by tile and leaves the most
significant tile (bottom right) untouched. How strongly each tile is mixed is fixed by its
position. Next: inside a weak tile, look for the operation, such as an addition of a
neighbouring tile's value (which would carry the corner's structure inward) or a fixed
multiplication. Test each candidate against the corner, where the values are known exactly.

## Layout and bit-level structure (2026-11-10, same 96 frames)

**The digit stream.** Carries cross from the right edge of one row (x = 639) into the left edge
of the row below (x = 480), correlation 0.49, and never into the neighbouring strip (0.005).
So the frame is four vertical strips, each 160 px wide and read like text: top to bottom, left to
right, blue < green < red. End to end that is 1,664 rows of 160 px, with the bottom-right pixel
most significant.

**Mixing follows 52-row blocks.** 1,664 = 32 × 52. Along the stream, the additive rate changes
only at multiples of 52 rows, or 16 or 36 rows past one:

| stream rows (blocks of 52) | rate |
|---|---|
| 8–12, 14–15.3, 16–20, 24–25.3, 27–28 | none (0.07) |
| the other blocks below 31 | weak (0.11–0.12) |
| 31–31.3 (16 rows) | 0.20 |
| 31.3–32 (36 rows) | exact (0.36) |

**The 0.20 band is exact in its low bits.** Second differences there are 0 mod 4 as often as in
the exact block (0.366 vs 0.369, and the same per channel). Bits 2 and 3 are each clean at about
three quarters of positions and disturbed at the rest, and which positions is fixed. So the
mixing is bitwise with a fixed pattern, like the masked shift-XOR steps of a Mersenne
Twister-style tempering. In the weak blocks all four bits are partly disturbed.

**Ruled out:**
- **Polynomial in L:** third to sixth differences are not zero anywhere outside the exact block.
- **Periodic in L:** rates are the same at spacings 1 to 15.
- **XOR with the plain values above:** XORing the band with the exact rows at any shift within
  the block, nibble- or bit-aligned, never raises the rate (best 0.13 against 0.20 untouched).
- **XOR with the lowest bits of the number:** the second differences show no systematic short
  period in L.
- **A repeating mask word:** the disturbed-bit pattern has no period up to 8,000 bits. About
  25–28% of bits 2 and 3 are disturbed, the density of an AND of two random bit streams.

**Open.** What the disturbed bits are XORed with. The candidates left are bits from lower blocks
(a left shift, whose sources are already mixed) and a product term (an AND, suggested by the 25%
density). Useful next data: frames far apart in L at one fixed low-digit pattern, and the two
demo pictures at known ~961,760-digit locations, where the whole location is known.
