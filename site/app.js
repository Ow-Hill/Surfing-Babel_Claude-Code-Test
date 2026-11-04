const $ = (id) => document.getElementById(id);
const BABELIA = "https://babelia.libraryofbabel.info/babelia.cgi?location=";
let THRESHOLD = 4;

const fmtInt = (n) => Number(n || 0).toLocaleString();
const fmtSigma = (s) => {
  if (s == null) return "–";
  if (s >= 10000) return ">10,000σ";
  return `${s >= 100 ? Math.round(s).toLocaleString() : s.toFixed(1)}σ`;
};
const shortLoc = (l) => (l.length > 16 ? `#${l.slice(0, 6)}…${l.slice(-6)}` : `#${l}`);
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

function tile({ img, loc, sigma, tier, caption, onClick }) {
  const b = document.createElement("button");
  b.className = "tile";
  b.innerHTML = `<img loading="lazy" alt=""><div class="meta"><span class="loc"></span><span class="tag"></span></div>`;
  b.querySelector("img").src = img;
  b.querySelector("img").alt = caption || `Babel location ${loc}`;
  b.querySelector(".loc").textContent = loc ? shortLoc(loc) : "test image";
  const tag = b.querySelector(".tag");
  tag.textContent = `${tier} · ${fmtSigma(sigma)}`;
  tag.classList.add(tierClass(tier));
  if (caption) {
    const c = document.createElement("div");
    c.className = "cap";
    c.textContent = caption;
    b.append(c);
  }
  b.onclick = onClick;
  return b;
}

function openDetail(f) {
  $("d-img").src = `data/${f.image}`;
  $("d-tier").textContent = f.tier;
  $("d-tier").className = `tag ${tierClass(f.tier)}`;
  $("d-sigma").textContent = fmtSigma(f.sigma);
  $("d-reason").textContent = `strongest signal: ${f.reason}`;
  $("d-loc").value = f.location;
  $("d-open").href = BABELIA + f.location;
  $("d-dl").href = `data/${f.image}`;
  $("d-dl").download = `babel-${f.location}.jpg`;
  const bars = $("d-tests");
  bars.innerHTML = "";
  const max = Math.max(THRESHOLD * 1.5, ...Object.values(f.tests));
  for (const [name, z] of Object.entries(f.tests).sort((a, b) => b[1] - a[1])) {
    const pct = Math.max(0, Math.min(100, (Math.log1p(Math.max(0, z)) / Math.log1p(max)) * 100));
    bars.insertAdjacentHTML("beforeend",
      `<span class="name">${name}</span><span class="track"><span class="fill" style="width:${pct}%"></span></span><span class="val">${fmtSigma(z)}</span>`);
  }
  $("detail").showModal();
}

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

async function load() {
  const [stats, finds, near, bench] = await Promise.all([
    getJSON("data/stats.json", {}), getJSON("data/finds.json", []),
    getJSON("data/near_misses.json", []), getJSON("benchmark/benchmark.json", null),
  ]);
  if (bench) THRESHOLD = bench.threshold_sigma;
  document.querySelectorAll(".thr").forEach((e) => (e.textContent = THRESHOLD));

  $("s-scanned").textContent = fmtInt(stats.scanned);
  $("s-finds").textContent = fmtInt(finds.length);
  $("s-best").textContent = stats.best ? fmtSigma(stats.best.sigma) : "–";
  $("s-last").textContent = ago(stats.last_run && stats.last_run.finished_at);
  renderHist(stats);

  $("finds").replaceChildren(...finds.map((f) =>
    tile({ img: `data/${f.image}`, loc: f.location, sigma: f.sigma, tier: f.tier, onClick: () => openDetail(f) })));
  $("finds-empty").hidden = finds.length > 0;
  $("finds-note").textContent = finds.length ? "click one for its location and test scores" : "";

  $("near").replaceChildren(...near.map((f) =>
    tile({ img: `data/${f.image}`, loc: f.location, sigma: f.sigma, tier: f.tier, onClick: () => openDetail(f) })));

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
}

$("d-copy").onclick = () => navigator.clipboard && navigator.clipboard.writeText($("d-loc").value);
window.reloadData = load;
load();
