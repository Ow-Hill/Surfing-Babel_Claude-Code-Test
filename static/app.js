const $ = (id) => document.getElementById(id);
let finds = [];
let knownIds = "";
let current = null;
let thresholdEdited = false;

const fmt = (n) => (n == null || !isFinite(n) ? "∞" : Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 }));

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.status === 204 ? null : r.json();
}

function renderSpark(scores, threshold) {
  const svg = $("spark");
  if (!scores.length) { svg.innerHTML = ""; return; }
  // log scale so static (~1) and strong finds (1000s) both fit
  const v = scores.map((s) => Math.log10(Math.max(1, isFinite(s) ? s : 1e4)));
  const max = Math.max(Math.log10(threshold) * 1.5, ...v, 0.5);
  const y = (x) => 58 - (x / max) * 56;
  const pts = v.map((x, i) => `${(i / Math.max(1, v.length - 1)) * 300},${y(x)}`).join(" ");
  const ty = y(Math.log10(threshold));
  svg.innerHTML = `<line x1="0" x2="300" y1="${ty}" y2="${ty}"/><polyline points="${pts}"/>`;
}

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    $("status").textContent = s.status;
    $("s-status").className = "dot" + (s.running ? " on" : s.status === "error" ? " err" : "");
    $("toggle").textContent = s.running ? "Stop scanning" : "Start scanning";
    $("toggle").dataset.running = s.running ? "1" : "";
    const scanned = s.totals.scanned || 0;
    $("scanned").textContent = fmt(scanned);
    $("found").textContent = fmt(s.totals.finds);
    $("rate").textContent = scanned ? fmt((s.totals.finds / scanned) * 1000) : "–";
    if (!thresholdEdited) $("threshold").value = s.threshold;
    renderSpark(s.recent_scores, s.threshold);
    const lf = s.last_frame;
    $("last-frame").textContent = lf ? `${lf.label} · score ${fmt(lf.score)} · seed ${lf.seed.slice(0, 60)}${lf.seed.length > 60 ? "…" : ""}` : "—";
    $("error").hidden = !s.error;
    $("error").textContent = s.error || "";
  } catch (e) {
    $("status").textContent = "server unreachable";
  }
}

async function refreshFinds() {
  const list = await api("/api/finds?limit=1000");
  const ids = list.map((f) => f.id).join(",");
  if (ids === knownIds) return;
  knownIds = ids;
  finds = list;
  renderGallery();
}

function renderGallery() {
  const sorted = [...finds];
  if ($("sort").value === "score") sorted.sort((a, b) => b.score - a.score);
  $("empty").hidden = sorted.length > 0;
  $("gallery").replaceChildren(...sorted.map((f) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `<img loading="lazy" alt="">
      <div class="meta"><span class="seed"></span><span class="tag"></span></div>`;
    card.querySelector("img").src = `/images/${f.image_file}`;
    card.querySelector(".seed").textContent = f.seed;
    const tag = card.querySelector(".tag");
    tag.textContent = `${f.label} · ${fmt(f.score)}`;
    if (f.label !== "structure") tag.classList.add("faint");
    card.onclick = () => openDetail(f);
    return card;
  }));
}

function openDetail(f) {
  current = f;
  $("d-img").src = `/images/${f.image_file}`;
  $("d-label").textContent = f.label;
  $("d-score").textContent = fmt(f.score);
  $("d-seed").value = f.seed;
  $("d-download").href = `/images/${f.image_file}`;
  const src = f.source_url || f.page_url;
  $("d-source").hidden = !src;
  if (src) $("d-source").href = src;
  const m = f.metrics;
  const rows = {
    "variance ratio": fmt(m.variance_ratio), "best block size": `${m.best_scale}px`,
    "neighbour corr.": fmt(m.neighbor_corr), "compress ratio": fmt(m.compress_ratio),
    "found": new Date(f.found_at * 1000).toLocaleString(),
  };
  $("d-metrics").innerHTML = "";
  for (const [k, v] of Object.entries(rows)) {
    const d = document.createElement("div");
    d.innerHTML = "<dt></dt><dd></dd>";
    d.querySelector("dt").textContent = k;
    d.querySelector("dd").textContent = v;
    $("d-metrics").append(d);
  }
  $("detail").showModal();
}

$("toggle").onclick = async () => {
  await api(`/api/scraper/${$("toggle").dataset.running ? "stop" : "start"}`, { method: "POST" });
  refreshStatus();
};
$("threshold").oninput = () => { thresholdEdited = true; };
$("threshold").onchange = async () => {
  try {
    await api("/api/threshold", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ threshold: parseFloat($("threshold").value) }) });
  } catch (e) { alert(e.message); }
  thresholdEdited = false;
};
$("sort").onchange = renderGallery;
$("d-copy").onclick = () => navigator.clipboard.writeText($("d-seed").value);
$("d-delete").onclick = async () => {
  if (!current || !confirm("Delete this find?")) return;
  await api(`/api/finds/${current.id}`, { method: "DELETE" });
  $("detail").close();
  knownIds = "";
  refreshFinds();
};

refreshStatus(); refreshFinds();
setInterval(refreshStatus, 1500);
setInterval(refreshFinds, 4000);
