/* "How the search works": a small, honest model of the scanner.
 *
 * Frames here are simulated in the browser (Babelia can't be read from a web
 * page): quarter-size static with 16 levels per channel, like Babel's, seeded
 * by the location number. Three simplified tests score each frame in σ, with
 * the same look-elsewhere correction the real detector uses, so pure static
 * reaches 4σ about as rarely here as it does for real.
 */
(function () {
  "use strict";
  const W = 160, H = 104, LEVELS = 16;            // a quarter of Babelia's 640 x 416
  const GX = 4, GY = 4;                            // brightness / smoothness cells
  const THRESHOLD = 4;
  const MAX_LOCATION = 9007199250000000;           // the slideshow's start range

  // --- numbers ---------------------------------------------------------------
  function hash(str) {                             // location string -> 32-bit seed
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  function rng(seed) {                             // mulberry32
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function erfc(x) {                               // Numerical Recipes, |error| < 1.2e-7
    const z = Math.abs(x), t = 1 / (1 + 0.5 * z);
    const r = t * Math.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 +
      t * (-0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 +
      t * (-0.82215223 + t * 0.17087277)))))))));
    return x >= 0 ? r : 2 - r;
  }
  const twoSidedP = (z) => erfc(Math.abs(z) / Math.SQRT2);
  function sigmaFromP(p) {                         // two-sided p -> σ (bisection: plenty fast here)
    if (p >= 1) return 0;
    let lo = 0, hi = 40;
    for (let i = 0; i < 60; i++) { const m = (lo + hi) / 2; if (twoSidedP(m) > p) lo = m; else hi = m; }
    return (lo + hi) / 2;
  }
  // Šidák: the strongest of n looks, as a frame-wide σ (what the real detector reports)
  function frameSigma(maxAbsZ, looks) {
    const p = twoSidedP(maxAbsZ);
    const pFrame = p < 1e-12 ? p * looks : 1 - Math.pow(1 - p, looks);
    return sigmaFromP(Math.min(1, pFrame));
  }

  // static's statistics, per value in level units (0..15)
  const MEAN = (LEVELS - 1) / 2, VAR = (LEVELS * LEVELS - 1) / 12;
  const MU4 = (() => { let s = 0; for (let v = 0; v < LEVELS; v++) s += (v - MEAN) ** 4; return s / LEVELS; })();

  // --- frames and signals ----------------------------------------------------
  function makeFrame(location, signal, strength) {
    const r = rng(hash(String(location)));
    const f = new Uint8Array(W * H * 3);
    for (let i = 0; i < f.length; i++) f[i] = Math.floor(r() * LEVELS);
    if (signal && signal !== "none" && strength > 0) hide(f, signal, strength);
    return f;
  }
  // blend a picture into static the way the benchmark does: (1-a)·static + a·picture
  function hide(f, signal, a) {
    const mix = (i, target) => { f[i] = Math.round((1 - a) * f[i] + a * target); };
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 3;
      if (signal === "disc") {
        const d = Math.hypot(x - 60, y - 39);            // inside one 40 x 26 cell
        if (d < 12) { mix(i, 15); mix(i + 1, 4); mix(i + 2, 3); }          // a reddish disc
      } else if (signal === "line") {
        if (y === 70 || y === 71) { mix(i, 15); mix(i + 1, 15); mix(i + 2, 15); }
      } else if (signal === "patch") {
        if (x >= 100 && x < 132 && y >= 20 && y < 46) { mix(i, 7.5); mix(i + 1, 7.5); mix(i + 2, 7.5); }
      }
    }
  }

  // --- three simplified tests --------------------------------------------------
  // per GX x GY cell: fn(sum, sumSq, n) over all channels, or per channel
  function cells(f, fn, perChannel = false) {
    const out = [], cw = W / GX, ch = H / GY;
    for (let gy = 0; gy < GY; gy++) for (let gx = 0; gx < GX; gx++) {
      const s = [0, 0, 0], q = [0, 0, 0];
      let n = 0;
      for (let y = gy * ch; y < (gy + 1) * ch; y++) for (let x = gx * cw; x < (gx + 1) * cw; x++) {
        const i = (y * W + x) * 3;
        for (let c = 0; c < 3; c++) { const v = f[i + c]; s[c] += v; q[c] += v * v; }
        n++;
      }
      if (perChannel) for (let c = 0; c < 3; c++) out.push(fn(s[c], q[c], n));
      else out.push(fn(s[0] + s[1] + s[2], q[0] + q[1] + q[2], 3 * n));
    }
    return out;
  }
  const maxAbs = (zs) => zs.reduce((m, z) => (Math.abs(z) > Math.abs(m) ? z : m), 0);

  function score(f) {
    // colour balance: is any region's red, green or blue off from static's average?
    const bright = maxAbs(cells(f, (s, q, n) => (s / n - MEAN) / Math.sqrt(VAR / n), true));
    // lines: is any row's average off?
    const rows = [];
    for (let y = 0; y < H; y++) {
      let s = 0;
      for (let i = y * W * 3; i < (y + 1) * W * 3; i++) s += f[i];
      const n = W * 3;
      rows.push((s / n - MEAN) / Math.sqrt(VAR / n));
    }
    const line = maxAbs(rows);
    // smoothness: is any region less (or more) varied than static?
    const smooth = maxAbs(cells(f, (s, q, n) => {
      const v = q / n - (s / n) ** 2;
      return (v - VAR) / Math.sqrt((MU4 - VAR * VAR) / n);
    }));
    const tests = [
      { id: "bright", name: "Colour balance", what: "a region with too much or too little of a colour", z: frameSigma(bright, GX * GY * 3) },
      { id: "line", name: "Lines", what: "a row whose average is off", z: frameSigma(line, H) },
      { id: "smooth", name: "Smoothness", what: "a region less varied than static", z: frameSigma(smooth, GX * GY) },
    ];
    const best = tests.reduce((a, b) => (b.z > a.z ? b : a));
    return { tests, best, sigma: best.z };
  }

  const api = { W, H, THRESHOLD, makeFrame, score, frameSigma, sigmaFromP, twoSidedP, hash };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }   // node tests

  // --- the component -----------------------------------------------------------
  const STEPS = [
    { title: "Pick a location",
      text: "Every frame in Babelia's slideshow has a number, its location. The scanner starts at a random one " +
            "below 9 quadrillion and counts up, frame by frame, just like the slideshow.", focus: "loc" },
    { title: "Babelia draws it",
      text: "Babelia turns the number into a 640 × 416 frame of random colours, 16 levels per channel, taking " +
            "about 10 seconds a frame. The scanner keeps a few requests going at once, never more than 40 a " +
            "minute. (Here the frame is simulated in your browser, at a quarter of the size.)", focus: "frame" },
    { title: "Score it",
      text: "The real detector runs 17 tests, each calibrated on real frames so it reads in σ: how far the frame " +
            "is from what static produces. Here are three simplified ones. Hide a signal and turn it up to " +
            "see which test notices first.", focus: "tests" },
    { title: "Keep the strange ones",
      text: "At 4σ or more a frame is a find. Before it counts, it is downloaded again and must match byte for " +
            "byte, so a damaged download can't become a find. 3 to 4σ is a near miss.", focus: "verdict" },
    { title: "Repeat, and publish",
      text: "Pure static almost never reaches 4σ, so finds are rare. Scan a thousand frames of pure static to " +
            "see how rare. The real scanner publishes what it finds here every 30 minutes.", focus: "batch" },
  ];

  const $ = (id) => document.getElementById(id);
  const root = $("search-demo");
  if (!root) return;
  const state = { step: 0, loc: 1 + Math.floor(Math.random() * MAX_LOCATION), signal: "none", strength: 0.3 };
  const canvas = $("sd-canvas"), ctx = canvas.getContext("2d");
  const img = ctx.createImageData(W, H);

  function fmtLoc(n) { return Number(n).toLocaleString("en-US"); }
  function draw(f) {
    for (let p = 0, i = 0; p < W * H; p++, i += 3) {
      img.data[p * 4] = f[i] * 17; img.data[p * 4 + 1] = f[i + 1] * 17; img.data[p * 4 + 2] = f[i + 2] * 17;
      img.data[p * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
  }
  function verdict(s) {
    if (s >= THRESHOLD) return { cls: "anomaly", text: `Find: ${s.toFixed(1)}σ. It would be re-downloaded to confirm, then saved.` };
    if (s >= 3) return { cls: "near", text: `Near miss: ${s.toFixed(1)}σ. Kept among the closest calls.` };
    return { cls: "static", text: `Static: ${s.toFixed(1)}σ. Counted and discarded.` };
  }

  function render() {
    const f = makeFrame(state.loc, state.signal, state.signal === "none" ? 0 : state.strength);
    draw(f);
    const r = score(f);
    $("sd-loc").textContent = fmtLoc(state.loc);
    $("sd-strength-out").textContent = `${Math.round(state.strength * 100)}%`;
    $("sd-strength").disabled = state.signal === "none";
    const max = 8;
    $("sd-tests").innerHTML = r.tests.map((t) => {
      const w = Math.min(100, (t.z / max) * 100);
      const over = t.z >= THRESHOLD ? " over" : t.z >= 3 ? " near" : "";
      return `<div class="sd-test${t === r.best ? " best" : ""}"><div class="sd-test-h"><b>${t.name}</b>` +
        `<span class="mono">${t.z.toFixed(1)}σ</span></div>` +
        `<div class="sd-bar" role="img" aria-label="${t.name} ${t.z.toFixed(1)} sigma">` +
        `<span class="sd-fill${over}" style="width:${w}%"></span>` +
        `<span class="sd-thr" style="left:${(THRESHOLD / max) * 100}%" title="4σ: a find"></span></div>` +
        `<small class="muted">${t.what}</small></div>`;
    }).join("");
    const v = verdict(r.sigma);
    const el = $("sd-verdict");
    el.className = `sd-verdict tag ${v.cls}`;
    el.textContent = v.text;
    // steps
    root.querySelectorAll(".sd-steps button").forEach((b, i) => {
      b.setAttribute("aria-current", i === state.step ? "step" : "false");
    });
    $("sd-step-title").textContent = `${state.step + 1}. ${STEPS[state.step].title}`;
    $("sd-step-text").textContent = STEPS[state.step].text;
    $("sd-prev").disabled = state.step === 0;
    $("sd-next").disabled = state.step === STEPS.length - 1;
    root.querySelectorAll("[data-focus]").forEach((n) => n.classList.toggle("sd-focus", n.dataset.focus === STEPS[state.step].focus));
  }

  // steps rail
  $("sd-steps").innerHTML = STEPS.map((s, i) =>
    `<li><button type="button" data-step="${i}"><span class="sd-num">${i + 1}</span> ${s.title}</button></li>`).join("");
  $("sd-steps").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-step]");
    if (b) { state.step = +b.dataset.step; render(); }
  });
  $("sd-prev").addEventListener("click", () => { state.step = Math.max(0, state.step - 1); render(); });
  $("sd-next").addEventListener("click", () => { state.step = Math.min(STEPS.length - 1, state.step + 1); render(); });
  // controls
  $("sd-next-loc").addEventListener("click", () => { state.loc += 1; render(); });
  $("sd-rand-loc").addEventListener("click", () => { state.loc = 1 + Math.floor(Math.random() * MAX_LOCATION); render(); });
  $("sd-signal").addEventListener("change", (e) => { state.signal = e.target.value; render(); });
  $("sd-strength").addEventListener("input", (e) => { state.strength = +e.target.value / 100; render(); });

  // a thousand frames of pure static, in small batches so the page stays responsive
  let running = false;
  $("sd-batch").addEventListener("click", () => {
    if (running) return;
    running = true;
    const N = 1000, bins = new Array(9).fill(0);
    let done = 0, near = 0, finds = 0, best = 0, start = state.loc;
    const out = $("sd-batch-out"), btn = $("sd-batch");
    btn.disabled = true;
    function batch() {
      const end = Math.min(N, done + 40);
      for (; done < end; done++) {
        const s = score(makeFrame(start + done, "none", 0)).sigma;
        bins[Math.min(8, Math.floor(s * 2))]++;
        if (s >= THRESHOLD) finds++; else if (s >= 3) near++;
        best = Math.max(best, s);
      }
      out.textContent = `Scanned ${done.toLocaleString("en-US")} frames…`;
      if (done < N) { setTimeout(batch, 0); return; }
      const peak = Math.max(...bins);
      $("sd-hist").innerHTML = bins.map((n, i) =>
        `<div class="sd-hbar${i >= 8 ? " over" : i >= 6 ? " near" : ""}" style="height:${Math.max(2, (n / peak) * 100)}%"` +
        ` title="${(i / 2).toFixed(1)}${i < 8 ? `–${((i + 1) / 2).toFixed(1)}` : "+"}σ: ${n} frames"></div>`).join("");
      $("sd-hist").hidden = false;
      $("sd-axis").hidden = false;
      out.textContent = `1,000 frames of pure static: ${finds} find${finds === 1 ? "" : "s"} (4σ or more), ` +
        `${near} near miss${near === 1 ? "" : "es"}, strongest ${best.toFixed(2)}σ. ` +
        "Static is expected to reach 4σ in these three tests about once in 5,000 frames, so finds stay rare " +
        "however long the scan runs, and any one of them is most likely chance.";
      state.loc = start + N;
      running = false; btn.disabled = false;
      render();
    }
    batch();
  });

  // the real scanner's pace, from the published stats (app.js calls this)
  window.searchDemoLive = function (stats) {
    const runs = (stats.runs || []).filter((r) => r.scanned && r.frames_per_hour);
    const last = runs[runs.length - 1];
    if (!last || !stats.scanned) return;
    const rate = stats.finds_total ? Math.round(stats.scanned / stats.finds_total) : null;
    $("sd-live").textContent = `The real scanner: ${stats.scanned.toLocaleString("en-US")} frames so far, ` +
      `about ${last.frames_per_hour.toLocaleString("en-US")} an hour lately` +
      (rate ? `, one find in every ${rate.toLocaleString("en-US")} frames.` : ".");
  };

  render();
})();
