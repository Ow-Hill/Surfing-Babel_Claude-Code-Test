const $ = (id) => document.getElementById(id);
const BABELIA_CGI = "https://babelia.libraryofbabel.info/babelia.cgi";
const BABELIA = `${BABELIA_CGI}?location=`;
const SLIDESHOW_MAX = 9007199250000000n;   // same start range as Babelia's slideshow
let THRESHOLD = 4;
let DATA = { finds: [], near: [], demos: [] };

// One plain-language name per test, used everywhere (description, bars,
// filters, overlay, benchmark) so the same thing is never called two names.
const TEST_INFO = {
  shape:      { name: "Shapes & regions", about: "areas, gradients and large shapes that survive averaging (4–64 px)" },
  blob:       { name: "Small objects", about: "the most extreme small patch anywhere in the frame (8–48 px)" },
  lines:      { name: "Rows & columns", about: "horizons, stripes and bands along rows or columns (1–8 px thick)" },
  angled:     { name: "Angled lines", about: "lines and bands at 15° steps between rows and columns" },
  contrast:   { name: "Contrast", about: "patches flatter or noisier than static, even when their colour blends in" },
  texture:    { name: "Texture", about: "neighbouring pixels that agree (smooth) or alternate (checkerboards)" },
  repetition: { name: "Repetition", about: "any part of the frame recurring at an offset: tiles, repeated motifs" },
  symmetry:   { name: "Symmetry", about: "mirror symmetry about any vertical or horizontal axis" },
  tone:       { name: "Colour balance", about: "too many or too few pixels in some colour range (too dark, too bright, too few colours)" },
  palette:    { name: "Colour correlation", about: "colour channels moving together (greyscale or limited palettes)" },
  spot:       { name: "Soft spots", about: "round or soft patches of any colour, 3–24 px (the eye's blob detector)" },
  curves:     { name: "Curves & strokes", about: "short line pieces in any direction: arcs, outlines, strokes" },
  flat:       { name: "Flat runs", about: "neighbouring pixels that nearly match: flat patches, single-colour strokes" },
  colours:    { name: "Few colours", about: "areas drawn with fewer colours than static allows (cartoons, text)" },
  spectrum:   { name: "Photo-like", about: "more large-scale variation than static, as in photos and soft shading" },
  face:       { name: "Face-like", about: "two spots above a third: the layout people read as a face" },
};
const N_TESTS = Object.keys(TEST_INFO).length;
const testName = (k) => (TEST_INFO[k] ? TEST_INFO[k].name : k);

const fmtInt = (n) => Number(n || 0).toLocaleString();
const fmtSigma = (s) => {
  if (s == null) return "–";
  if (s >= 10000) return ">10,000σ";
  return `${s >= 100 ? Math.round(s).toLocaleString() : s.toFixed(1)}σ`;
};
const shortLoc = (l) => (l.length > 16 ? `#${l.slice(0, 6)}…${l.slice(-6)}` : `#${l}`);
// "Statistically unusual static, not a picture: a darker spot ... (4.6σ). Also ..."
// -> "A darker spot ..." for tile captions
const shortDesc = (d) => {
  if (!d) return "";
  let s = d.replace(/^[^:]*:\s*/, "").split(/\.\s/)[0].replace(/\s*\([\d.,]+σ\)\.?$/, "");
  return s.charAt(0).toUpperCase() + s.slice(1);
};
const tierClass = (t) => ({ "near miss": "near" }[t] || t);
const ago = (t) => {
  if (!t) return "–";
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
};

// Loads report failure instead of silently returning empty data (which
// would look like a real "nothing found").
let loadFailed = false;
async function getJSON(path, fallback, required = true) {
  try {
    const r = await fetch(path, { cache: "no-cache" });
    if (r.ok) return await r.json();
    if (required) loadFailed = true;
  } catch { if (required) loadFailed = true; }
  return fallback;
}

// "faint" is also a score tier, so the eye labels avoid that word
const VIS_TEXT = { likely: "visible", faint: "barely visible", invisible: "invisible to the eye" };

function tile({ img, loc, label, sigma, tier, caption, onClick, visibility }) {
  const b = document.createElement("button");
  b.className = "tile";
  b.innerHTML = `<img loading="lazy" alt=""><div class="meta"><span class="tag"></span><span class="loc"></span></div>`;
  b.querySelector("img").src = img;
  // a visible caption already describes the tile: don't make screen readers read it twice
  b.querySelector("img").alt = caption ? "" : `Babel location ${loc}`;
  b.querySelector(".loc").textContent = label || (loc ? shortLoc(loc) : "test image");
  const tag = b.querySelector(".tag");
  if (tier) {
    tag.textContent = `${tier} · ${fmtSigma(sigma)}`;
    tag.classList.add(tierClass(tier));
  } else tag.remove();
  if (visibility && visibility.label) {
    const v = document.createElement("span");
    v.className = `vis vis-${visibility.label}`;
    v.textContent = VIS_TEXT[visibility.label] || visibility.label;
    v.title = (visibility.ratio != null ? `Contrast ${visibility.ratio}× what static itself shows at the same size ` : "") +
      `(≥2 likely visible at a glance, 1.4–2 barely visible). A simple model: it under-rates thin lines`;
    b.querySelector(".meta").append(v);
  }
  if (caption) {
    const c = document.createElement("div");
    c.className = "cap";
    c.textContent = caption;
    b.append(c);
  }
  b.onclick = onClick;
  return b;
}

// "I see something": a pre-filled GitHub issue; the scan workflow scores the
// frame, replies on the issue and adds it to "People noticed".
const FLAG_REPO = "Ow-Hill/Surfing-Babel_Claude-Code-Test";
function flagUrl(loc) {
  if (!loc || !/^\d{1,40}$/.test(String(loc))) return null;
  const body = `Location: ${loc}\n\nWhat do you see? (optional)\n\n` +
    `Seen on ${location.origin}${location.pathname}#${loc}`;
  return `https://github.com/${FLAG_REPO}/issues/new?` + new URLSearchParams({ title: `Flag: ${loc}`, body });
}

// ---------------------------------------------------------------- detail view
// item: { key, kind, location?, locationFile?, locationPreview?, preview, full?, maps?, mask?,
//         description?, sigma?, tier?, reason?, tests?, note? }
let current = null;
let nav = { list: [], index: -1, label: "" };   // what ‹ › step through

function setHash(key) {
  const url = key ? `#${key}` : location.pathname + location.search;
  history.replaceState(null, "", url);
}

async function fullLocation(item) {
  if (item.location) return item.location;
  if (!item._loc) item._loc = (await (await fetch(item.locationFile)).text()).trim();
  return item._loc;
}

function openDetail(item, list = null, label = "") {
  if (list) nav = { list, index: list.findIndex((x) => x.key === item.key), label };
  else if (!nav.list.some((x) => x.key === item.key)) nav = { list: [item], index: 0, label: "" };
  else nav.index = nav.list.findIndex((x) => x.key === item.key);
  current = item;
  setHash(item.key);

  const n = nav.list.length;
  $("d-title").textContent = n > 1 ? `${nav.label || "Frame"} ${nav.index + 1} of ${n}` : (nav.label || "Frame");
  $("d-prev").disabled = n < 2;
  $("d-next").disabled = n < 2;

  const img = $("d-img");
  img.onerror = () => {
    if (current !== item) return;
    $("d-note").textContent = "Babelia didn't return this image (it may be busy or down). Try “Open in Babelia” in a moment.";
    $("d-note").hidden = false;
  };
  img.src = item.preview;
  img.alt = item.description || `Babel location ${item.location || ""}`;
  $("d-note").textContent = item.note || "";
  $("d-note").hidden = !item.note;

  // full-resolution frame from Babelia (finds store only a small preview)
  $("d-full").hidden = true;
  if (item.full) {
    $("d-full").textContent = "Showing the stored preview; loading the full image from Babelia…";
    $("d-full").hidden = false;
    const full = new Image();
    full.onload = () => { if (current === item) { img.src = full.src; $("d-full").hidden = true; } };
    full.onerror = () => {
      if (current === item) $("d-full").textContent = "Showing the stored preview: Babelia didn't respond. “Open in Babelia” may work later.";
    };
    full.src = item.full;
  }

  $("d-desc").textContent = item.description || "";
  $("d-desc").hidden = !item.description;
  Overlay.show(item);

  $("d-score").hidden = item.sigma == null;
  if (item.sigma != null) {
    $("d-tier").textContent = item.tier;
    $("d-tier").className = `tag ${tierClass(item.tier)}`;
    $("d-sigma").textContent = fmtSigma(item.sigma);
    $("d-reason").textContent = `strongest test: ${testName(item.reason)}` +
      (item.sigma_global != null ? ` · ${fmtSigma(item.sigma_global)} for the whole frame, allowing for ${N_TESTS} tests` : "") +
      (item.visibility ? ` · to the eye: ${({ likely: "likely visible", faint: "barely visible", invisible: "invisible" })[item.visibility.label] || item.visibility.label}` +
        (item.visibility.ratio != null ? ` (${item.visibility.ratio}× static's flicker)` : "") : "") +
      (item.combined != null ? ` · all tests combined: ${fmtSigma(item.combined)}` : "");
  }

  $("d-loc").value = item.location || item.locationPreview;
  const flag = flagUrl(item.location);
  $("d-flag").hidden = !flag;
  if (flag) $("d-flag").href = flag;
  $("d-dl").hidden = !item.download;
  if (item.download) {
    $("d-dl").href = item.download;
    $("d-dl").download = item.downloadName;
  }

  const bars = $("d-tests");
  bars.innerHTML = "";
  $("d-tests-box").hidden = !item.tests;
  if (item.tests) {
    const max = Math.max(THRESHOLD * 1.5, ...Object.values(item.tests));
    for (const [k, z] of Object.entries(item.tests).sort((a, b) => b[1] - a[1])) {
      const pct = Math.max(0, Math.min(100, (Math.log1p(Math.max(0, z)) / Math.log1p(max)) * 100));
      const row = document.createElement("div");
      row.className = "bar-row";
      row.innerHTML = `<span class="name"></span><span class="track"><span class="fill" style="width:${pct}%"></span></span><span class="val">${fmtSigma(z)}</span>`;
      row.querySelector(".name").textContent = testName(k);
      row.querySelector(".name").title = TEST_INFO[k] ? TEST_INFO[k].about : "";
      bars.append(...row.children);
    }
  }
  if (!$("detail").open) $("detail").showModal();
}

function step(delta) {
  const n = nav.list.length;
  if (n < 2) return;
  nav.index = (nav.index + delta + n) % n;
  openDetail(nav.list[nav.index]);
}

const findItem = (f, kind = "Find") => ({
  key: f.location, kind, location: f.location, preview: `data/${f.image}`, full: BABELIA + f.location,
  maps: f.maps ? `data/${f.maps}` : null, mask: f.mask ? `data/${f.mask}` : null,
  description: f.description, sigma: f.sigma, tier: f.tier, reason: f.reason, tests: f.tests,
  combined: f.combined, sigma_global: f.sigma_global, visibility: f.visibility,
  download: `data/${f.image}`, downloadName: `babel-${f.location}-preview.jpg`,
});

const demoItem = (d) => ({
  key: `demo-${d.slug}`, kind: "Demo", locationFile: d.locationFile || d.location_file,
  locationPreview: `${d.location_preview} (${fmtInt(d.location_digits)} digits)`,
  preview: d.image, maps: d.maps, mask: d.mask, description: d.description,
  sigma: d.sigma, tier: d.tier, reason: d.reason, tests: d.tests,
  download: d.image, downloadName: `babel-demo-${d.slug}.jpg`,
  combined: d.combined,
  note: `${d.blurb ? d.blurb + " " : ""}Seeded demo: we drew this picture, got its location from Babelia's image search, then fetched it back from that location with the scanner. It was not found by scanning.`,
});

const lookupItem = (loc) => ({
  key: loc, kind: "Location", location: loc, preview: BABELIA + loc, download: null,
  note: "This location isn't in our finds; it's shown straight from Babelia, without a score.",
});

function openFromHash() {
  let key;
  try { key = decodeURIComponent(location.hash.slice(1)); } catch { return; }   // malformed link
  if (!key || key === "how") return;
  const fi = DATA.finds.find((x) => x.location === key);
  if (fi) {
    // the current filter may hide it: then step through all finds
    const list = visibleFinds().some((f) => f.location === key) ? visibleFinds() : DATA.finds;
    return openDetail(findItem(fi), list.map((f) => findItem(f)), "Find");
  }
  const fl = (DATA.flags || []).find((x) => x.location === key);
  if (fl) return openDetail(findItem(fl, "Flagged"), DATA.flags.map((f) => findItem(f, "Flagged")), "Flagged");
  const ni = DATA.near.find((x) => x.location === key);
  if (ni) return openDetail(findItem(ni, "Near miss"), DATA.near.map((f) => findItem(f, "Near miss")), "Near miss");
  for (const [t, board] of Object.entries(DATA.leaders || {})) {
    const li = board.find((x) => x.location === key);
    const name = t === "combined" ? "All tests combined" : testName(t);
    if (li) return openDetail(findItem(li, name), board.map((f) => findItem(f, name)), `${name} leader`);
  }
  const d = DATA.demos.find((x) => `demo-${x.slug}` === key);
  if (d) return openDetail(demoItem(d), DATA.demos.map(demoItem), "Demo");
  if (/^\d{1,4000}$/.test(key)) openDetail(lookupItem(key.replace(/^0+(?=\d)/, "")), null, "Location");
}

// Babelia's GET URL can't carry a ~1 MB location, so open long ones by POST.
async function openInBabelia() {
  const loc = await fullLocation(current);
  if (loc.length < 2000) { window.open(BABELIA + loc, "_blank", "noopener"); return; }
  const form = document.createElement("form");
  form.method = "POST"; form.action = BABELIA_CGI; form.target = "_blank";
  const input = document.createElement("input");
  input.type = "hidden"; input.name = "location"; input.value = loc;
  form.append(input); document.body.append(form); form.submit(); form.remove();
}

async function copyText(text, btn) {
  const old = btn.textContent;
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = "Copied";
  } catch {
    btn.textContent = "Couldn't copy";
  }
  setTimeout(() => (btn.textContent = old), 1400);
}

$("d-copy").onclick = async () => copyText(await fullLocation(current), $("d-copy"));
$("d-link").onclick = () => copyText(location.href, $("d-link"));
$("d-open").onclick = openInBabelia;
$("d-prev").onclick = () => step(-1);
$("d-next").onclick = () => step(1);
$("d-close").onclick = () => $("detail").close();
$("detail").addEventListener("close", () => {
  current = null; Overlay.reset(); setHash(null);
  // opened from a link on page load: nothing had focus to go back to
  if (document.activeElement === document.body || !document.activeElement) {
    const h = document.getElementById("finds-h");
    if (h) { h.tabIndex = -1; h.focus({ preventScroll: true }); }
  }
});
// Clicking outside a dialog (on the dimmed backdrop) closes it and returns
// to the home page. Uses the click position, since a click on the backdrop
// and on the dialog's own padding both report the dialog as the target.
function closeOnBackdrop(dialog) {
  dialog.addEventListener("click", (e) => {
    if (e.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
    if (!inside) dialog.close();
  });
}
closeOnBackdrop($("detail"));
closeOnBackdrop($("scan-dialog"));
$("detail").addEventListener("keydown", (e) => {
  if (e.target.matches("input[type=range], textarea, select")) return;
  if (e.key === "ArrowLeft") { step(-1); e.preventDefault(); }
  else if (e.key === "ArrowRight") { step(1); e.preventDefault(); }
  else if (e.key === "e" || e.key === "E") Overlay.toggle();
  else if (/^[1-6]$/.test(e.key)) Overlay.setLayerIndex(+e.key - 1);
});
Overlay.init(testName);
window.addEventListener("hashchange", openFromHash);

// ---------------------------------------------------------------- histogram
let histView = "all";
let HIST_DATA = null;

// Bars: real frames scored by the current detector. Dots: what pure static
// should give (the benchmark's simulated frames), scaled to the same total.
function renderHist(stats, bench) {
  HIST_DATA = { stats, bench };
  const all = histView === "all";
  // SVG elements ignore the .hidden property: toggle the attribute itself
  $("hist-scroll").toggleAttribute("hidden", !all);
  $("hist-tests").toggleAttribute("hidden", all);
  document.querySelectorAll("#hist-view button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === histView)));
  if (all) renderOverall(stats, bench); else renderPerTest(stats);
}

function renderOverall(stats, bench) {
  const svg = $("hist");
  const cur = stats.hist_current;
  const edges = (cur && cur.edges) || stats.hist_edges || [];
  const counts = (cur && cur.counts) || stats.hist || [];
  const n = counts.reduce((a, b) => a + b, 0);
  const total = stats.scanned || 0;
  if (!n) {
    svg.innerHTML = `<text x="0" y="20">No frames scored by the current detector yet.</text>`;
    $("hist-caption").textContent = "";
    $("hist-legend").textContent = "";
    return;
  }
  const since = cur && cur.since ? ` since ${new Date(cur.since * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" })}` : "";
  $("hist-caption").textContent =
    `${fmtInt(n)} frames scored by the current detector${since}, each counted by its strongest test.` +
    (total > n ? ` Frames scored by an older version of the detector (${fmtInt(total - n)}) aren't shown.` : "") +
    (n < 1000 ? " A small sample so far; it fills in as the scans run." : "") +
    ` Each test is calibrated on real Babel frames, so pure static would put about ${fmtExpected(n * pMaxOver(THRESHOLD, N_TESTS))} ` +
    `frames at ${THRESHOLD}σ or more.`;

  // expected for pure static, from the benchmark's simulated frames
  const nullHist = bench && bench.null && bench.null.hist && JSON.stringify(bench.null.hist_edges) === JSON.stringify(edges)
    ? bench.null.hist : null;
  const nullN = nullHist ? nullHist.reduce((a, b) => a + b, 0) : 0;
  const expected = nullHist && nullN ? nullHist.map((c) => (c / nullN) * n) : null;

  const num = (v) => String(v).replace("-", "−");
  const labels = counts.map((_, i) => i === 0 ? `<${num(edges[0])}` : i === counts.length - 1 ? `≥${num(edges[i - 1])}`
    : `${num(edges[i - 1])} to ${num(edges[i])}`);
  const W = 1000, H = 230, padB = 44, padT = 24;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  const k = counts.length, bw = W / k;
  const maxLog = Math.log10(Math.max(...counts, ...(expected || [0])) + 1);
  const y = (c) => H - padB - (maxLog ? (Math.log10(c + 1) / maxLog) * (H - padB - padT) : 0);
  let out = `<line class="axis" x1="0" x2="${W}" y1="${H - padB}" y2="${H - padB}"/>`;
  counts.forEach((c, i) => {
    const x = i * bw + 3, top = y(c);
    const over = i > 0 && edges[i - 1] >= THRESHOLD;
    out += `<rect class="bar${over ? " over" : ""}" x="${x}" y="${top}" width="${bw - 6}" height="${H - padB - top}">` +
      `<title>${labels[i]}σ: ${fmtInt(c)} frames${expected ? `; pure static would give about ${expected[i] < 1 && expected[i] > 0 ? expected[i].toFixed(2) : fmtInt(Math.round(expected[i]))}` : ""}</title></rect>`;
    // count above whichever is higher, the bar or the expected-static dot
    const labelY = Math.min(top, expected ? y(expected[i]) - 6 : top) - 5;
    if (c) out += `<text x="${x + (bw - 6) / 2}" y="${labelY}" text-anchor="middle">${fmtInt(c)}</text>`;
    out += `<text x="${x + (bw - 6) / 2}" y="${H - padB + 16}" text-anchor="middle">${labels[i]}</text>`;
  });
  if (expected) {
    const pts = expected.map((e, i) => [i * bw + bw / 2, y(e)]);
    out += `<polyline class="expect" points="${pts.map((p) => p.join(",")).join(" ")}"/>`;
    out += pts.map(([px, py], i) => expected[i] > 0.05 ? `<circle class="expect-dot" cx="${px}" cy="${py}" r="4"/>` : "").join("");
  }
  out += `<text x="${W / 2}" y="${H - 4}" text-anchor="middle" class="axis-title">score (σ) of each frame's strongest test · bar heights on a log scale</text>`;
  const ti = edges.indexOf(THRESHOLD);
  if (ti >= 0) {
    const x = (ti + 1) * bw;
    out += `<line class="thr" x1="${x}" x2="${x}" y1="${padT - 16}" y2="${H - padB}"/>` +
           `<text x="${x + 6}" y="${padT - 8}" class="thr-label">find threshold (${THRESHOLD}σ) →</text>`;
  }
  svg.innerHTML = out;
  $("hist-legend").innerHTML = `<span class="key-bar"></span>real Babel frames` +
    (expected ? ` <span class="key-dot"></span>what pure static gives (${fmtInt(nullN)} simulated frames, scaled to the same total)` : "") +
    ` <span class="key-thr"></span>find threshold`;
}

// One small chart per test, from the per-test score histograms (0.25σ bins,
// regrouped into 1σ bins for readability).
function renderPerTest(stats) {
  const th = stats.test_hist;
  const box = $("hist-tests");
  if (!th || !th.counts || !Object.keys(th.counts).length) {
    box.innerHTML = "<p class='muted'>Per-test scores are recorded from the next scan on.</p>";
    $("hist-caption").textContent = ""; $("hist-legend").textContent = "";
    return;
  }
  const LO = -3, HI = 10;                       // 1σ bins: <−3, −3..−2, …, 9..10, ≥10
  const regroup = (c) => {
    const out = new Array(HI - LO + 2).fill(0);
    c.forEach((v, i) => {
      if (!v) return;
      const z = i === 0 ? -Infinity : i === c.length - 1 ? Infinity : th.lo + (i - 1) * th.step;
      const b = z < LO ? 0 : z >= HI ? out.length - 1 : 1 + Math.floor(z - LO);
      out[b] += v;
    });
    return out;
  };
  const order = [...Object.keys(TEST_INFO), "combined"].filter((t) => th.counts[t]);
  const n = th.counts[order[0]].reduce((a, b) => a + b, 0);
  const present = order.filter((t) => t !== "combined").length;
  $("hist-caption").textContent = `${fmtInt(n)} frames, each scored by all ${present} tests shown. Each test is calibrated on ` +
    `real Babel frames, so on pure static every one should look like the same bell curve, with about ` +
    `${fmtExpected(n * pMaxOver(THRESHOLD, 1))} frames per test at ${THRESHOLD}σ or more.`;
  $("hist-legend").innerHTML = `<span class="key-bar"></span>frames per 1σ band (log scale) <span class="key-thr"></span>${THRESHOLD}σ`;
  box.replaceChildren(...order.map((t) => {
    const raw = th.counts[t], bins = regroup(raw), total = raw.reduce((a, b) => a + b, 0);
    const over = rateAtLeast(th, t, THRESHOLD);
    const W = 300, H = 96, padB = 16, bw = W / bins.length;
    const maxLog = Math.log10(Math.max(...bins) + 1) || 1;
    let svg = `<line class="axis" x1="0" x2="${W}" y1="${H - padB}" y2="${H - padB}"/>`;
    bins.forEach((c, i) => {
      const h = (Math.log10(c + 1) / maxLog) * (H - padB - 6);
      const lo = LO + i - 1;
      const isOver = i > 0 && lo >= THRESHOLD;
      svg += `<rect class="bar${isOver ? " over" : ""}" x="${i * bw + 1}" y="${H - padB - h}" width="${bw - 2}" height="${h}">` +
        `<title>${i === 0 ? `<${LO}` : i === bins.length - 1 ? `≥${HI}` : `${lo} to ${lo + 1}`}σ: ${fmtInt(c)}</title></rect>`;
    });
    const tx = (THRESHOLD - LO + 1) * bw;
    svg += `<line class="thr" x1="${tx}" x2="${tx}" y1="0" y2="${H - padB}"/>`;
    [0, 4, 8].forEach((z) => (svg += `<text x="${(z - LO + 1) * bw}" y="${H - 3}" text-anchor="start">${z}</text>`));
    const fig = document.createElement("figure");
    fig.className = "mini-hist";
    fig.innerHTML = `<figcaption><b></b><span class="muted small"></span></figcaption><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"></svg>`;
    fig.querySelector("b").textContent = t === "combined" ? "All tests combined" : testName(t);
    fig.querySelector(".muted").textContent = (over && over.k ? `${fmtInt(over.k)} of ${fmtInt(total)} at ${THRESHOLD}σ+` : `none of ${fmtInt(total)} at ${THRESHOLD}σ+`) +
      ` (static: ~${fmtExpected(total * pMaxOver(THRESHOLD, 1))})`;
    fig.querySelector("svg").setAttribute("aria-label", `${fig.querySelector("b").textContent}: score distribution`);
    fig.querySelector("svg").innerHTML = svg;
    return fig;
  }));
}

document.querySelectorAll("#hist-view button").forEach((b) => (b.onclick = () => {
  histView = b.dataset.view;
  if (HIST_DATA) renderHist(HIST_DATA.stats, HIST_DATA.bench);
}));

// ---------------------------------------------------------------- live strip
// Frames straight from Babelia, walking locations like its slideshow does.
// Display only: Babelia sends no CORS headers, so the browser can't score them.
// People who ask for reduced motion get a still live strip until they press Play.
const REDUCED_MOTION = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
const live = { loc: null, playing: !REDUCED_MOTION, loading: false, history: [], failures: 0 };

function randomStart() {
  const a = new Uint32Array(2);
  crypto.getRandomValues(a);
  return ((BigInt(a[0]) << 32n) | BigInt(a[1])) % SLIDESHOW_MAX;
}

function liveVisible() {
  const r = $("live").getBoundingClientRect();
  return !document.hidden && r.bottom > 0 && r.top < innerHeight;
}

function livePlaceholder(text) {
  // only shown until the first frame arrives; afterwards the last frame stays
  if ($("live-img").hidden) $("live-ph").textContent = text;
}

function liveNext() {
  if (live.loading || !live.playing) return;
  if (!liveVisible()) { setTimeout(liveNext, 1500); return; }
  live.loading = true;
  live.loc = live.loc == null ? randomStart() : live.loc + 1n;
  const loc = live.loc.toString();
  $("live-status").textContent = `loading the next frame (Babelia takes 5–20 s)…`;
  livePlaceholder("Loading the first frame from Babelia (5–20 s)…");
  const img = new Image();
  const done = (ok) => {
    live.loading = false;
    if (ok) {
      live.failures = 0;
      $("live-img").src = img.src;
      $("live-img").hidden = false;
      $("live-ph").hidden = true;
      $("live-img").dataset.loc = loc;
      $("live-loc").textContent = `#${loc}`;
      $("live-flag").href = flagUrl(loc);
      $("live-flag").hidden = false;
      $("live-status").textContent = "";
      live.history = [loc, ...live.history].slice(0, 6);
      $("live-history").replaceChildren(...live.history.slice(1).map((l) =>
        tile({ img: BABELIA + l, loc: l, onClick: () => openDetail(lookupItem(l), null, "Location") })));
    } else {
      live.failures += 1;
      const wait = Math.min(60, 8 * live.failures);
      $("live-status").textContent = `Babelia didn't respond; trying again in ${wait} s.`;
      livePlaceholder(`Babelia isn't responding right now. Trying again in ${wait} s…`);
    }
    setTimeout(liveNext, ok ? 2500 : Math.min(60, 8 * live.failures) * 1000);
  };
  img.onload = () => done(true);
  img.onerror = () => done(false);
  img.src = BABELIA + loc;
}

$("live-toggle").onclick = () => {
  live.playing = !live.playing;
  $("live-toggle").textContent = live.playing ? "Pause" : "Play";
  $("live-status").textContent = live.playing ? "" : "paused";
  livePlaceholder(live.playing ? "Connecting to Babelia…" : "Paused");
  if (live.playing) liveNext();
};
document.querySelector(".live-frame").onclick = () => {
  const l = $("live-img").dataset.loc;
  if (l) openDetail(lookupItem(l), null, "Location");
};

// ---------------------------------------------------------------- finds: sort + filter
const view = { sort: "visible", filter: "all" };
const visRatio = (f) => (f.visibility && f.visibility.ratio) || 0;

function visibleFinds() {
  let list = [...DATA.finds];
  if (view.filter !== "all") list = list.filter((f) => f.reason === view.filter);
  list.sort(view.sort === "new" ? (a, b) => b.found_at - a.found_at
    : view.sort === "visible" ? (a, b) => visRatio(b) - visRatio(a) || b.sigma - a.sigma
    : (a, b) => b.sigma - a.sigma);
  return list;
}

function renderFinds(totalFinds) {
  const finds = DATA.finds;
  if (view.filter !== "all" && !finds.some((f) => f.reason === view.filter)) view.filter = "all";
  const list = visibleFinds();
  const items = list.map((f) => findItem(f));
  $("finds").replaceChildren(...list.map((f, i) =>
    tile({ img: `data/${f.image}`, loc: f.location, sigma: f.sigma, tier: f.tier, visibility: f.visibility,
           caption: shortDesc(f.description), onClick: () => openDetail(items[i], items, "Find") })));
  $("finds-controls").hidden = finds.length < 2;
  // filter chips by strongest test, with counts
  const counts = {};
  finds.forEach((f) => (counts[f.reason] = (counts[f.reason] || 0) + 1));
  const chips = [["all", `All (${finds.length})`],
    ...Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, c]) => [k, `${testName(k)} (${c})`])];
  $("finds-filter").replaceChildren(...chips.map(([k, text]) => {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = text;
    b.setAttribute("aria-pressed", String(view.filter === k));
    b.onclick = () => { view.filter = k; renderFinds(totalFinds); };
    return b;
  }));
  $("finds-empty").hidden = finds.length > 0;
  const shown = view.filter === "all" ? "" : ` · showing ${list.length} (${testName(view.filter)})`;
  $("finds-note").textContent = !finds.length ? ""
    : (totalFinds > finds.length ? `top ${finds.length} of ${fmtInt(totalFinds)} by score` : `${finds.length} so far`) + shown;
}
$("finds-sort").onchange = (e) => { view.sort = e.target.value; renderFinds(DATA.totalFinds); };

// Chance that pure static puts the strongest of `k` independent tests at or
// above `z` sigma (upper normal tail, Abramowitz-Stegun 26.2.17).
function pMaxOver(z, k) {
  const t = 1 / (1 + 0.2316419 * z);
  const tail = Math.exp(-z * z / 2) / Math.sqrt(2 * Math.PI) *
    t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  return 1 - Math.pow(1 - tail, k);
}
// Frames scanned in the 24 hours up to the latest run, and how much of that
// time a scan was running (scans relay into each other, so this should be near 100%).
function scanPace(runs) {
  const scans = (runs || []).filter((r) => r.scanned && r.finished_at && r.started_at);
  if (!scans.length) return "";
  const end = scans[scans.length - 1].finished_at;
  const span = Math.min(86400, end - scans[0].started_at), from = end - span;
  if (span < 3600) return "";
  const day = scans.filter((r) => r.finished_at > from);
  const frames = day.reduce((n, r) => n + r.scanned, 0);
  const busy = day.reduce((t, r) => t + (r.finished_at - Math.max(r.started_at, from)), 0);
  const perDay = Math.round(frames * 86400 / span / 100) * 100;
  return ` · about ${fmtInt(perDay)} frames a day, scanning ${Math.round(100 * Math.min(1, busy / span))}% of the time`;
}

function fmtExpected(x) { return x >= 10 ? fmtInt(Math.round(x)) : x >= 1 ? x.toFixed(1) : x.toFixed(2); }

// ---------------------------------------------------------------- leaderboards
// share of frames at or above `z` for one test, from its histogram
function rateAtLeast(th, test, z) {
  const counts = th && th.counts && th.counts[test];
  if (!counts) return null;
  const n = counts.reduce((a, b) => a + b, 0);
  if (!n) return null;
  const from = 1 + Math.floor((z - th.lo) / th.step);
  const k = counts.slice(from).reduce((a, b) => a + b, 0);
  return { k, n };
}

function renderLeaders(leaders, stats) {
  const order = [...Object.keys(TEST_INFO), "combined"].filter((t) => (leaders[t] || []).length);
  $("leaders-section").hidden = !order.length;
  const EYE = new Set(["spot", "curves", "flat", "colours", "spectrum", "face"]);
  const heading = (text) => { const h = document.createElement("h3"); h.className = "board-group"; h.textContent = text; return h; };
  const boards = order.map((t) => {
    const board = leaders[t];
    const name = t === "combined" ? "All tests combined" : testName(t);
    const about = t === "combined" ? "weak evidence spread across several tests, summed" : TEST_INFO[t].about;
    const r = rateAtLeast(stats.test_hist, t, THRESHOLD);
    const rate = !r ? "" : r.k ? `${fmtInt(r.k)} of ${fmtInt(r.n)} frames reached ${THRESHOLD}σ`
      : `none of ${fmtInt(r.n)} frames reached ${THRESHOLD}σ`;
    const items = board.map((f) => findItem(f, name));
    const box = document.createElement("div");
    box.className = "board";
    const head = document.createElement("div");
    head.className = "board-head";
    head.innerHTML = "<b></b><span class='muted small'></span><span class='muted small rate'></span>";
    head.children[0].textContent = name;
    head.children[1].textContent = about;
    head.children[2].textContent = rate;
    const row = document.createElement("div");
    row.className = "gallery mini";
    row.append(...board.map((f, i) => {
      const score = t === "combined" ? f.combined : (f.tests || {})[t];
      return tile({ img: `data/${f.image}`, loc: f.location, sigma: score, tier: tierOf(score),
                    onClick: () => openDetail(items[i], items, `${name} leader`) });
    }));
    box.append(head, row);
    box.dataset.test = t;
    return box;
  });
  // two groups: what the eye might see, then the statistical tests (and combined)
  const eye = boards.filter((b) => EYE.has(b.dataset.test));
  const stat = boards.filter((b) => !EYE.has(b.dataset.test));
  $("leaders").replaceChildren(
    ...(eye.length ? [heading("What the eye might see"), ...eye] : []),
    ...(stat.length ? [heading("Statistical tests"), ...stat] : []));
}
const tierOf = (s) => s >= 40 ? "striking" : s >= 16 ? "clear" : s >= 8 ? "faint" : s >= THRESHOLD ? "anomaly" : s >= 3 ? "near miss" : "static";

function renderFingerprint(stats) {
  const f = stats.fingerprint;
  if (!f || !f.n) { $("fp-note").textContent = ""; return; }
  const mean = f.sum / f.n, sd = Math.sqrt(Math.max(0, f.sumsq / f.n - mean * mean));
  $("fp-note").textContent =
    `Babelia's fingerprint (red ↔ green and green ↔ blue, 156 rows apart) averaged +${mean.toFixed(1)} ` +
    `(spread ${sd.toFixed(1)}) over ${fmtInt(f.n)} frames; the weakest was +${f.min.value.toFixed(1)}. ` +
    `Simulated static scores about 0, so a real frame without it would be news.`;
}

// ---------------------------------------------------------------- data
async function load() {
  loadFailed = false;
  const [stats, finds, near, bench, demos, leaders, flags] = await Promise.all([
    getJSON("data/stats.json", {}), getJSON("data/finds.json", []),
    getJSON("data/near_misses.json", []), getJSON("benchmark/benchmark.json", null, false),
    getJSON("demo/demo.json", [], false), getJSON("data/leaders.json", {}, false),
    getJSON("data/flags.json", [], false),
  ]);
  $("load-error").hidden = !loadFailed;
  if (bench) THRESHOLD = bench.threshold_sigma;      // before anything that uses it renders
  DATA = { finds, near, demos, leaders, flags };
  $("flags-section").hidden = !flags.length;
  const flagItems = flags.map((f) => ({ ...findItem(f, "Flagged"),
    note: `Flagged by ${f.votes} ${f.votes === 1 ? "person" : "people"}${f.note ? `: “${f.note}”` : ""}.` }));
  $("flags").replaceChildren(...flags.map((f, i) =>
    tile({ img: `data/${f.image}`, loc: f.location, sigma: f.sigma, tier: f.tier, visibility: f.visibility,
           caption: `${f.votes} ${f.votes === 1 ? "vote" : "votes"}${f.note ? ` · ${f.note}` : ""}`,
           onClick: () => openDetail(flagItems[i], flagItems, "Flagged") })));
  renderLeaders(leaders, stats);
  renderFingerprint(stats);
  document.querySelectorAll(".thr").forEach((e) => (e.textContent = THRESHOLD));

  $("s-scanned").textContent = fmtInt(stats.scanned);
  const totalFinds = stats.finds_total ?? finds.length;
  DATA.totalFinds = totalFinds;
  $("s-finds").textContent = fmtInt(totalFinds);
  // measured rate, not a promise
  const rate = totalFinds && stats.scanned ? `1 in ${fmtInt(Math.round(stats.scanned / totalFinds / 100) * 100 || 1)} frames` : "rate still being measured";
  window.SB_FRAMES_PER_FIND = totalFinds && stats.scanned ? stats.scanned / totalFinds : null;
  $("s-rate").textContent = rate;
  document.querySelectorAll(".rate-text").forEach((e) => (e.textContent = totalFinds ? `about ${rate}` : "very rarely (the exact rate is still being measured)"));
  $("s-best").textContent = stats.best ? fmtSigma(stats.best.sigma) : "–";
  $("s-last").textContent = ago(stats.last_run && stats.last_run.finished_at);
  $("s-pace").textContent = scanPace(stats.runs);
  renderHist(stats, bench);

  renderFinds(totalFinds);
  $("finds-empty").textContent = loadFailed ? "Finds couldn't be loaded (see the message above)."
    : `None yet under the current detector. Real Babel static very rarely reaches ${THRESHOLD}σ; every frame's scores ` +
      `are logged, so the true rate will appear here as scans run. Meanwhile, the leaderboards below show the strangest frames for each test.`;

  const nearItems = near.map((f) => findItem(f, "Near miss"));
  $("near").replaceChildren(...near.map((f, i) =>
    tile({ img: `data/${f.image}`, loc: f.location, sigma: f.sigma, tier: f.tier,
           caption: shortDesc(f.description), onClick: () => openDetail(nearItems[i], nearItems, "Near miss") })));

  $("demo-section").hidden = !demos.length;
  const demoItems = demos.map(demoItem);
  $("demos").replaceChildren(...demos.map((d, i) =>
    tile({ img: d.image, label: "seeded demo", sigma: d.sigma, tier: d.tier, caption: d.title,
           onClick: () => openDetail(demoItems[i], demoItems, "Demo") })));

  $("tests-list").replaceChildren(...Object.entries(TEST_INFO).map(([, v]) => {
    const d = document.createElement("div");
    d.innerHTML = "<b></b><span></span>";
    d.querySelector("b").textContent = v.name;
    d.querySelector("span").textContent = v.about;
    return d;
  }));

  if (bench) {
    const n = bench.null;
    $("null-note").textContent =
      `Calibration check: of ${fmtInt(n.frames)} frames of simulated Babel static, ${n.false_positives} reached the ` +
      `${bench.threshold_sigma}σ threshold and the highest scored ${n.max_sigma}σ. Real Babel static is a little ` +
      `quieter than the simulation.`;
    $("bench").replaceChildren(...(bench.examples || []).map((e) =>
      tile({ img: `benchmark/${e.thumb || e.file}`, sigma: e.sigma, tier: e.tier, caption: e.label,
             onClick: () => window.open(`benchmark/${e.file}`, "_blank") })));
    const rows = bench.signals.map((r) =>
      `<tr><td>${r.signal}</td><td>${r.strength === "-" ? "–" : Math.round(r.strength * 100) + "%"}</td>` +
      `<td>${r.detected}/${r.trials}</td><td>${fmtSigma(r.min_sigma)}</td><td>${testName(r.reason)}</td>` +
      `<td>${r.visible ? `<span class="vis vis-${r.visible}">${VIS_TEXT[r.visible]}</span> ${r.visibility}×` : "–"}</td></tr>`).join("");
    $("bench-table").innerHTML =
      `<thead><tr><th>Hidden signal</th><th>Strength</th><th>Detected</th><th>Weakest score</th><th>Caught by</th>` +
      `<th title="Contrast against static's own flicker at the same size; ≥2 likely visible at a glance">To the eye</th></tr></thead><tbody>${rows}</tbody>`;
    const beaten = bench.signals.filter((r) => r.visible && r.visible !== "likely" && r.detected === r.trials).length;
    if (beaten) $("null-note").textContent += ` Detector vs eye: ${beaten} of these signals are caught every time ` +
      `while still invisible or only faint to a person.`;
  }
  if (!$("detail").open) openFromHash();
}

$("load-retry").onclick = () => load();
window.reloadData = load;
load();
if (live.playing) liveNext();
else {                     // reduced motion: wait for Play
  $("live-toggle").textContent = "Play";
  $("live-status").textContent = "paused (reduced motion)";
  livePlaceholder("Paused: press Play to stream frames from Babelia");
}
