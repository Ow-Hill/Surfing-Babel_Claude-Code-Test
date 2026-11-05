const $ = (id) => document.getElementById(id);
const BABELIA_CGI = "https://babelia.libraryofbabel.info/babelia.cgi";
const BABELIA = `${BABELIA_CGI}?location=`;
const SLIDESHOW_MAX = 9007199250000000n;   // same start range as Babelia's slideshow
let THRESHOLD = 4;
let DATA = { finds: [], near: [], demos: [] };

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
  s = s.charAt(0).toUpperCase() + s.slice(1);
  return s.length > 110 ? s.slice(0, 107) + "…" : s;
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

async function getJSON(path, fallback) {
  try {
    const r = await fetch(path, { cache: "no-cache" });
    return r.ok ? await r.json() : fallback;
  } catch { return fallback; }
}

function tile({ img, loc, label, sigma, tier, caption, onClick }) {
  const b = document.createElement("button");
  b.className = "tile";
  b.innerHTML = `<img loading="lazy" alt=""><div class="meta"><span class="loc"></span><span class="tag"></span></div>`;
  b.querySelector("img").src = img;
  b.querySelector("img").alt = caption || `Babel location ${loc}`;
  b.querySelector(".loc").textContent = label || (loc ? shortLoc(loc) : "test image");
  const tag = b.querySelector(".tag");
  if (tier) {
    tag.textContent = `${tier} · ${fmtSigma(sigma)}`;
    tag.classList.add(tierClass(tier));
  } else tag.remove();
  if (caption) {
    const c = document.createElement("div");
    c.className = "cap";
    c.textContent = caption;
    b.append(c);
  }
  b.onclick = onClick;
  return b;
}

// ---------------------------------------------------------------- detail view
// item: { key, location?, locationFile?, locationPreview?, preview, full?, maps?, mask?, description?,
//         sigma?, tier?, reason?, tests?, note? }
let current = null;

function setHash(key) {
  const url = key ? `#${key}` : location.pathname + location.search;
  history.replaceState(null, "", url);
}

async function fullLocation(item) {
  if (item.location) return item.location;
  if (!item._loc) item._loc = (await (await fetch(item.locationFile)).text()).trim();
  return item._loc;
}

function openDetail(item) {
  current = item;
  setHash(item.key);
  const img = $("d-img");
  img.src = item.preview;
  $("d-note").textContent = item.note || "";
  $("d-note").hidden = !item.note;

  // full-resolution frame from Babelia (finds store only a small preview)
  $("d-full").hidden = true;
  if (item.full) {
    $("d-full").textContent = "preview: loading full image from Babelia…";
    $("d-full").hidden = false;
    const full = new Image();
    full.onload = () => { if (current === item) { img.src = full.src; $("d-full").hidden = true; } };
    full.onerror = () => {
      if (current === item) $("d-full").textContent = "preview (Babelia didn't respond; try “Open in Babelia”)";
    };
    full.src = item.full;
  }

  // description + evidence overlay
  $("d-desc").textContent = item.description || "";
  $("d-desc").hidden = !item.description;
  Overlay.show(item);

  // score
  $("d-score").hidden = item.sigma == null;
  if (item.sigma != null) {
    $("d-tier").textContent = item.tier;
    $("d-tier").className = `tag ${tierClass(item.tier)}`;
    $("d-sigma").textContent = fmtSigma(item.sigma);
    $("d-reason").textContent = `strongest signal: ${item.reason}`;
  }

  // location
  $("d-loc").value = item.location || item.locationPreview;
  $("d-dl").hidden = !item.download;
  if (item.download) {
    $("d-dl").href = item.download;
    $("d-dl").download = item.downloadName;
  }

  // per-test bars
  const bars = $("d-tests");
  bars.innerHTML = "";
  if (item.tests) {
    const max = Math.max(THRESHOLD * 1.5, ...Object.values(item.tests));
    for (const [name, z] of Object.entries(item.tests).sort((a, b) => b[1] - a[1])) {
      const pct = Math.max(0, Math.min(100, (Math.log1p(Math.max(0, z)) / Math.log1p(max)) * 100));
      bars.insertAdjacentHTML("beforeend",
        `<span class="name">${name}</span><span class="track"><span class="fill" style="width:${pct}%"></span></span><span class="val">${fmtSigma(z)}</span>`);
    }
  }
  if (!$("detail").open) $("detail").showModal();
}

const findItem = (f) => ({
  key: f.location, location: f.location, preview: `data/${f.image}`, full: BABELIA + f.location,
  maps: f.maps ? `data/${f.maps}` : null, mask: f.mask ? `data/${f.mask}` : null,
  description: f.description, sigma: f.sigma, tier: f.tier, reason: f.reason, tests: f.tests,
  download: `data/${f.image}`, downloadName: `babel-${f.location}-preview.jpg`,
});

const demoItem = (d) => ({
  key: `demo-${d.slug}`, locationFile: d.locationFile || d.location_file,
  locationPreview: `${d.location_preview} (${fmtInt(d.location_digits)} digits)`,
  preview: d.image, maps: d.maps, mask: d.mask, description: d.description,
  sigma: d.sigma, tier: d.tier, reason: d.reason, tests: d.tests,
  download: d.image, downloadName: `babel-demo-${d.slug}.jpg`,
  note: `Seeded demo: we drew this picture, got its location from Babelia's image search, then fetched it back from that location with the scanner. It was not found by scanning.`,
});

const lookupItem = (loc) => ({
  key: loc, location: loc, preview: BABELIA + loc, download: null,
  note: "This location isn't in our finds; it's shown straight from Babelia, without a score.",
});

function openFromHash() {
  const key = decodeURIComponent(location.hash.slice(1));
  if (!key) return;
  const f = DATA.finds.find((x) => x.location === key) || DATA.near.find((x) => x.location === key);
  if (f) return openDetail(findItem(f));
  const d = DATA.demos.find((x) => `demo-${x.slug}` === key);
  if (d) return openDetail(demoItem(d));
  if (/^\d{1,4000}$/.test(key)) openDetail(lookupItem(key.replace(/^0+(?=\d)/, "")));
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
  try {
    await navigator.clipboard.writeText(text);
    const old = btn.textContent; btn.textContent = "Copied";
    setTimeout(() => (btn.textContent = old), 1200);
  } catch {}
}

$("d-copy").onclick = async () => copyText(await fullLocation(current), $("d-copy"));
$("d-link").onclick = () => copyText(location.href, $("d-link"));
$("d-open").onclick = openInBabelia;
$("detail").addEventListener("close", () => { current = null; Overlay.reset(); setHash(null); });
Overlay.init();
window.addEventListener("hashchange", openFromHash);

// ---------------------------------------------------------------- histogram
function renderHist(stats) {
  const svg = $("hist");
  const edges = stats.hist_edges || [];
  const counts = stats.hist || [];
  if (!counts.length || !stats.scanned) {
    svg.innerHTML = `<text x="0" y="20">No frames scanned yet.</text>`;
    return;
  }
  const labels = ["<" + edges[0], ...edges.map((e, i) => (i < edges.length - 1 ? `${e}` : `≥${e}`))];
  const W = 1000, H = 190, padB = 24, padT = 16;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  const n = counts.length, bw = W / n;
  const maxLog = Math.log10(Math.max(...counts) + 1);
  let out = `<line class="axis" x1="0" x2="${W}" y1="${H - padB}" y2="${H - padB}"/>`;
  counts.forEach((c, i) => {
    const h = maxLog ? (Math.log10(c + 1) / maxLog) * (H - padB - padT) : 0;
    const x = i * bw + 3, y = H - padB - h;
    const over = i > 0 && edges[i - 1] >= THRESHOLD;
    out += `<rect class="bar${over ? " over" : ""}" x="${x}" y="${y}" width="${bw - 6}" height="${h}"><title>${labels[i]}σ: ${fmtInt(c)} frames</title></rect>`;
    if (c) out += `<text x="${x + (bw - 6) / 2}" y="${y - 4}" text-anchor="middle">${fmtInt(c)}</text>`;
    out += `<text x="${x + (bw - 6) / 2}" y="${H - 6}" text-anchor="middle">${labels[i]}</text>`;
  });
  const ti = edges.indexOf(THRESHOLD);
  if (ti >= 0) {
    const x = (ti + 1) * bw;
    out += `<line class="thr" x1="${x}" x2="${x}" y1="${padT - 10}" y2="${H - padB}"/>`;
  }
  svg.innerHTML = out;
}

// ---------------------------------------------------------------- live strip
// Frames straight from Babelia, walking locations like its slideshow does.
// Display only: Babelia sends no CORS headers, so the browser can't score them.
const live = { loc: null, playing: true, loading: false, history: [] };

function randomStart() {
  const a = new Uint32Array(2);
  crypto.getRandomValues(a);
  return ((BigInt(a[0]) << 32n) | BigInt(a[1])) % SLIDESHOW_MAX;
}

function liveVisible() {
  const r = $("live").getBoundingClientRect();
  return !document.hidden && r.bottom > 0 && r.top < innerHeight;
}

function liveNext() {
  if (live.loading || !live.playing) return;
  if (!liveVisible()) { setTimeout(liveNext, 1500); return; }
  live.loading = true;
  live.loc = live.loc == null ? randomStart() : live.loc + 1n;
  const loc = live.loc.toString();
  $("live-status").textContent = `loading #${loc}…`;
  const img = new Image();
  const done = (ok) => {
    live.loading = false;
    if (ok) {
      $("live-img").src = img.src;
      $("live-img").dataset.loc = loc;
      $("live-loc").textContent = `#${loc}`;
      $("live-status").textContent = "";
      live.history = [loc, ...live.history].slice(0, 6);
      $("live-history").replaceChildren(...live.history.slice(1).map((l) =>
        tile({ img: BABELIA + l, loc: l, onClick: () => openDetail(lookupItem(l)) })));
    } else {
      $("live-status").textContent = "Babelia didn't respond; retrying…";
    }
    setTimeout(liveNext, ok ? 2500 : 8000);
  };
  img.onload = () => done(true);
  img.onerror = () => done(false);
  img.src = BABELIA + loc;
}

$("live-toggle").onclick = () => {
  live.playing = !live.playing;
  $("live-toggle").textContent = live.playing ? "Pause" : "Play";
  if (live.playing) liveNext();
};
$("live-img").onclick = () => {
  const l = $("live-img").dataset.loc;
  if (l) openDetail(lookupItem(l));
};

// ---------------------------------------------------------------- data
async function load() {
  const [stats, finds, near, bench, demos] = await Promise.all([
    getJSON("data/stats.json", {}), getJSON("data/finds.json", []),
    getJSON("data/near_misses.json", []), getJSON("benchmark/benchmark.json", null),
    getJSON("demo/demo.json", []),
  ]);
  DATA = { finds, near, demos };
  if (bench) THRESHOLD = bench.threshold_sigma;
  document.querySelectorAll(".thr").forEach((e) => (e.textContent = THRESHOLD));

  $("s-scanned").textContent = fmtInt(stats.scanned);
  const totalFinds = stats.finds_total ?? finds.length;
  $("s-finds").textContent = fmtInt(totalFinds);
  $("s-best").textContent = stats.best ? fmtSigma(stats.best.sigma) : "–";
  $("s-last").textContent = ago(stats.last_run && stats.last_run.finished_at);
  renderHist(stats);

  const byScore = [...finds].sort((a, b) => b.sigma - a.sigma);
  $("finds").replaceChildren(...byScore.map((f) =>
    tile({ img: `data/${f.image}`, loc: f.location, sigma: f.sigma, tier: f.tier,
           caption: shortDesc(f.description), onClick: () => openDetail(findItem(f)) })));
  $("finds-empty").hidden = finds.length > 0;
  $("finds-note").textContent = !finds.length ? ""
    : totalFinds > finds.length ? `top ${finds.length} of ${fmtInt(totalFinds)} by score`
    : "highest score first";

  $("near").replaceChildren(...near.map((f) =>
    tile({ img: `data/${f.image}`, loc: f.location, sigma: f.sigma, tier: f.tier, onClick: () => openDetail(findItem(f)) })));

  $("demo-section").hidden = !demos.length;
  $("demos").replaceChildren(...demos.map((d) =>
    tile({ img: d.image, label: "seeded demo", sigma: d.sigma, tier: d.tier, caption: d.title,
           onClick: () => openDetail(demoItem(d)) })));

  if (bench) {
    const n = bench.null;
    $("null-note").textContent =
      `Calibration check: of ${fmtInt(n.frames)} frames of simulated Babel static, ${n.false_positives} reached the ` +
      `${bench.threshold_sigma}σ threshold (expected: about 1 in 400); the highest scored ${n.max_sigma}σ.`;
    $("bench").replaceChildren(...(bench.examples || []).map((e) =>
      tile({ img: `benchmark/${e.file}`, sigma: e.sigma, tier: e.tier, caption: e.label,
             onClick: () => window.open(`benchmark/${e.file}`, "_blank") })));
    const rows = bench.signals.map((r) =>
      `<tr><td>${r.signal}</td><td>${r.strength === "-" ? "–" : Math.round(r.strength * 100) + "%"}</td>` +
      `<td>${r.detected}/${r.trials}</td><td>${fmtSigma(r.min_sigma)}</td><td>${r.reason}</td></tr>`).join("");
    $("bench-table").innerHTML =
      `<thead><tr><th>Hidden signal</th><th>Strength</th><th>Detected</th><th>Weakest score</th><th>Caught by</th></tr></thead><tbody>${rows}</tbody>`;
  }
  if (!$("detail").open) openFromHash();
}

window.reloadData = load;
load();
liveNext();
