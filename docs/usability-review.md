# Usability review: Nielsen's 10 heuristics

Review of https://ow-hill.github.io/Surfing-Babel_Claude-Code-Test/ on desktop (1280 px) and
phone (390 px), covering the home page, the detail dialog with its evidence overlay, the
live strip, the Scan now dialog and failure states (Babelia unreachable, GitHub API limits).

Severity uses Nielsen's scale:
- **0** not a problem
- **1** cosmetic
- **2** minor
- **3** major, important to fix
- **4** catastrophe

**Status** says whether this change fixed it.

## 1. Visibility of system status

| # | Problem | Sev. | Status |
|---|---|---|---|
| 1.1 | The scan status says "scanning: started 65 min ago" with no hint of when results will appear or what is happening, and no way to see the run. | 2 | Fixed: links to the live run on GitHub and says results appear when the run finishes. |
| 1.2 | While the live strip loads, or when Babelia is slow, it shows a large black box with a broken-image icon. The status ("retrying…") is small text off to the side. | 3 | Fixed: a placeholder inside the frame says what is happening (loading / retrying / paused). No broken-image icon. |
| 1.3 | If `stats.json` or `finds.json` fails to load, the page silently shows "–" and "Nothing yet", which looks like a real empty result. | 3 | Fixed: a visible error banner with a Retry button. |
| 1.4 | When GitHub's API rate limit is hit (60 requests/hour per visitor), the status only says "scan status unavailable". | 2 | Fixed: says it's GitHub's rate limit and when it will try again. |

## 2. Match between system and the real world

| # | Problem | Sev. | Status |
|---|---|---|---|
| 2.1 | Internal test names (`tone`, `blob`, `palette`) appear as "strongest signal: tone", in the score bars and in the benchmark table. The overlay calls the same thing "Colour balance". | 3 | Fixed: plain names everywhere (Colour balance, Small objects, Shapes & regions…), consistent between the description, bars, overlay layers and benchmark. |
| 2.2 | "σ" is never explained where it's used, and the histogram axis labels ("<-2", "-2", …) don't say they are score ranges. | 2 | Fixed: a "What's σ?" explainer by the stats, an axis title, range labels and a labelled threshold line. |
| 2.3 | Copy claims "about 1 in 400 frames" reach the threshold. Measured on real frames it's about 1 in 2,000, so the page sets the wrong expectation. | 2 | Fixed: the rate is computed from the actual scan totals. |

## 3. User control and freedom

| # | Problem | Sev. | Status |
|---|---|---|---|
| 3.1 | In the detail dialog the close button sits on the image and scrolls away on phones. The only other exit is Esc, which phones don't have. | 3 | Fixed: a sticky header bar with Close that stays visible while scrolling. |
| 3.2 | There's no way to step between finds; you must close and reopen. | 2 | Fixed: Previous/Next buttons with "3 of 12", plus the ← / → keys. |
| 3.3 | After moving the threshold slider there's no way back to the default. | 1 | Fixed: a Reset button. |

## 4. Consistency and standards

| # | Problem | Sev. | Status |
|---|---|---|---|
| 4.1 | The section is "Closest calls" but its tag says "near miss". | 1 | Fixed: "Closest calls (near misses)". |
| 4.2 | Test naming differs between the description, the overlay, the bars and the docs (see 2.1). | 2 | Fixed with 2.1. |

## 5. Error prevention

| # | Problem | Sev. | Status |
|---|---|---|---|
| 5.1 | The Scan now dialog doesn't say what will happen: how long until results, or that scheduled scans wait while it runs. A 3-hour scan can be started by mistake. | 2 | Fixed: a live summary under the length picker ("~250 frames; results in about 25 min; scheduled scans wait"). |
| 5.2 | Lowering the overlay threshold below static's level paints static speckle. | 1 | Already handled: the note warns about it. Unchanged. |

## 6. Recognition rather than recall

| # | Problem | Sev. | Status |
|---|---|---|---|
| 6.1 | Tier tags (anomaly, faint, clear…) are explained only in a table near the bottom of the page. | 2 | Fixed: a compact tier legend next to the Finds heading. |
| 6.2 | Overlay colours (orange = noisier, blue = flatter, pink = selected range…) are only described in a sentence. | 2 | Fixed: colour-key chips for each layer. |

## 7. Flexibility and efficiency of use

| # | Problem | Sev. | Status |
|---|---|---|---|
| 7.1 | Finds can't be sorted or filtered. With up to 100, finding e.g. the newest or all symmetry finds is slow. | 2 | Fixed: sort (score / newest) and filter by strongest test, with counts. |
| 7.2 | No keyboard shortcuts in the detail view. | 1 | Fixed: ← / → for previous/next, E to show or hide evidence, 1–5 to choose layers. |

## 8. Aesthetic and minimalist design

| # | Problem | Sev. | Status |
|---|---|---|---|
| 8.1 | The page is ~6,400 px tall on desktop. The 40-row benchmark table and the long detection explainer dominate, though few visitors need them. | 3 | Fixed: "How detection works", thresholds and the benchmark are collapsible sections, closed by default. |
| 8.2 | The detail dialog always lists all ten test scores, pushing the actions far down. | 2 | Fixed: the scores are under "All ten test scores" (collapsed). The actions sit right after the description. |

## 9. Help users recognise, diagnose and recover from errors

| # | Problem | Sev. | Status |
|---|---|---|---|
| 9.1 | A rejected token shows the API message but no next step. | 2 | Fixed: the message links to the token-creation page and the "Run workflow" fallback. |
| 9.2 | The data-load failure is invisible (see 1.3). | 3 | Fixed with 1.3. |

## 10. Help and documentation

| # | Problem | Sev. | Status |
|---|---|---|---|
| 10.1 | No quick orientation: a newcomer must read the whole page to learn that finds are almost always unusual static, not pictures. | 2 | Fixed: a one-line "How to read this page" under the header, linking to the detection explainer. |

## Not changed

- The live strip still can't score frames: Babelia sends no CORS headers, so a browser can't read its pixels. The page says so.
- Finds are still mostly colour-balance anomalies. That's a detector property, not a UI one; see the README.
