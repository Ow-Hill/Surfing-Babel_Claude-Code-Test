// Evidence overlay for the detail view.
//
// Each kept frame has a maps.json (computed from the full-resolution frame
// at scan time). This draws it over the image as one of several layers,
// with a per-layer threshold in sigma. The default threshold is lowered
// per frame when needed, so the frame's strongest evidence is always visible.

const Overlay = (() => {
  const $ = (id) => document.getElementById(id);
  const LAYERS = {
    mean:     { label: "Shapes & objects", tests: ["shape", "blob"], slider: true,
                hint: "16 px areas whose average brightness or colour departs from static" },
    contrast: { label: "Contrast", tests: ["contrast"], slider: true,
                hint: "areas flatter (blue) or noisier (orange) than static" },
    rows:     { label: "Rows & columns", tests: ["lines"], slider: true,
                hint: "4 px bands of rows or columns that are brighter or darker than static" },
    geometry: { label: "Lines, repeats & symmetry", tests: ["angled", "repetition", "symmetry"], slider: true,
                hint: "the strongest angled line, repeat offset and mirror axis" },
    tone:     { label: "Colour balance", tests: ["tone", "palette", "texture"], slider: true,
                hint: "pink: every pixel in the colour range that is most over- or under-represented (chart below); the rest is dimmed. They are spread evenly, so this anomaly has no shape" },
  };
  const DEFAULT_LAYER = {
    shape: "mean", blob: "mean", contrast: "contrast", lines: "rows", angled: "geometry",
    repetition: "geometry", symmetry: "geometry", tone: "tone", palette: "tone", texture: "tone",
  };
  const STATIC_LIMIT = { mean: 3, contrast: 3, rows: 3, geometry: 3, tone: 3 };
  // colour key per layer (recognition, not recall)
  const KEYS = {
    mean:     [["hsl(55,95%,55%)", "just over threshold"], ["hsl(0,95%,55%)", "strongest"]],
    contrast: [["hsl(30,95%,55%)", "noisier than static"], ["hsl(215,95%,55%)", "flatter than static"]],
    rows:     [["hsl(40,95%,55%)", "brighter band"], ["hsl(220,95%,55%)", "darker band"]],
    geometry: [["rgb(255,70,70)", "angled line"], ["rgb(80,220,255)", "repeat offset"], ["rgb(190,110,255)", "mirror axis"]],
    tone:     [["rgb(255,60,210)", "pixels in the flagged colour range"], ["rgb(40,36,50)", "everything else (dimmed)"]],
  };
  let nameOf = (k) => k;

  let state = null;   // { item, maps, mask, layer, thr: {layer: value} }

  const maxAbs = (a) => a.reduce((m, v) => Math.max(m, Math.abs(v)), 0);

  function layerMax(layer, maps) {
    switch (layer) {
      case "mean": return maxAbs(maps.mean);
      case "contrast": return maxAbs(maps.contrast);
      case "rows": return Math.max(maxAbs(maps.rows), maxAbs(maps.cols));
      case "geometry": return Math.max(Math.abs(maps.angled.z || 0), Math.abs(maps.repeat.z || 0), maps.mirror.z || 0);
      case "tone": return maxAbs(maps.tone.flat());
    }
    return 0;
  }

  // Default: where static rarely reaches, but never above 60% of this
  // frame's own peak, so there is always something to see.
  function defaultThreshold(layer, maps) {
    const d = (maps.defaults && maps.defaults[layer]) || STATIC_LIMIT[layer];
    return Math.max(1.5, Math.min(d, Math.round(layerMax(layer, maps) * 0.6 * 10) / 10));
  }

  function ramp(t, hueFrom, hueTo) {
    const h = hueFrom + (hueTo - hueFrom) * t;
    return `hsla(${h}, 95%, 55%, ${0.28 + 0.5 * t})`;
  }

  function drawCells(ctx, maps, values, thr, signedColours) {
    const [gw, gh] = maps.grid, cell = maps.cell;
    const off = document.createElement("canvas");
    off.width = gw; off.height = gh;
    const o = off.getContext("2d");
    const top = Math.max(thr * 2, maxAbs(values));
    values.forEach((v, i) => {
      if (Math.abs(v) < thr) return;
      const t = Math.min(1, (Math.abs(v) - thr) / Math.max(0.1, top - thr));
      o.fillStyle = signedColours ? (v > 0 ? ramp(t, 40, 20) : ramp(t, 200, 225)) : ramp(t, 55, 0);
      o.fillRect(i % gw, Math.floor(i / gw), 1, 1);
    });
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(off, 0, 0, gw * cell, gh * cell);
  }

  function drawRows(ctx, maps, thr) {
    const [w, h] = maps.size, b = maps.row_band;
    const top = Math.max(thr * 2, maxAbs(maps.rows), maxAbs(maps.cols));
    const paint = (v, x, y, ww, hh) => {
      if (Math.abs(v) < thr) return;
      const t = Math.min(1, (Math.abs(v) - thr) / Math.max(0.1, top - thr));
      ctx.fillStyle = v > 0 ? ramp(t, 50, 30) : ramp(t, 210, 230);
      ctx.fillRect(x, y, ww, hh);
    };
    maps.rows.forEach((v, i) => paint(v, 0, i * b, w, b));
    maps.cols.forEach((v, i) => paint(v, i * b, 0, b, h));
  }

  function label(ctx, text, x, y) {
    ctx.font = "bold 15px ui-monospace, monospace";
    ctx.lineWidth = 4; ctx.strokeStyle = "rgba(0,0,0,.75)"; ctx.fillStyle = "#fff";
    ctx.strokeText(text, x, y); ctx.fillText(text, x, y);
  }

  function drawGeometry(ctx, maps, thr) {
    const [w, h] = maps.size;
    const notes = [];
    const a = maps.angled;
    if (a.line && a.line.length === 4 && Math.abs(a.z) >= thr) {
      const [x0, y0, x1, y1] = a.line;
      ctx.strokeStyle = "rgba(255, 70, 70, .8)"; ctx.lineWidth = Math.max(4, a.band * 2);
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
      label(ctx, `line ${Math.abs(a.z).toFixed(1)}σ`, (x0 + x1) / 2 + 8, (y0 + y1) / 2 - 8);
    } else notes.push("angled line");
    const r = maps.repeat;
    if (Math.abs(r.z || 0) >= thr) {
      const cx = w / 2, cy = h / 2;
      ctx.strokeStyle = "rgba(80, 220, 255, .9)"; ctx.lineWidth = 3;
      ctx.setLineDash([8, 6]);
      ctx.strokeRect(cx - 60, cy - 40, 120, 80);
      ctx.strokeRect(cx - 60 + r.dx, cy - 40 + r.dy, 120, 80);
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + r.dx, cy + r.dy); ctx.stroke();
      label(ctx, `repeats (${r.dx}, ${r.dy}) ${Math.abs(r.z).toFixed(1)}σ`, cx - 60, cy - 48);
    } else notes.push("repeat");
    const m = maps.mirror;
    if ((m.z || 0) >= thr) {
      ctx.strokeStyle = "rgba(190, 110, 255, .95)"; ctx.lineWidth = 3; ctx.setLineDash([12, 8]);
      ctx.beginPath();
      if (m.axis === "vertical") { ctx.moveTo(m.pos, 0); ctx.lineTo(m.pos, h); }
      else { ctx.moveTo(0, m.pos); ctx.lineTo(w, m.pos); }
      ctx.stroke(); ctx.setLineDash([]);
      label(ctx, `mirror ${m.z.toFixed(1)}σ`, m.axis === "vertical" ? m.pos + 8 : 12, m.axis === "vertical" ? 24 : m.pos - 8);
    } else notes.push("mirror axis");
    return notes;
  }

  // The stored mask is black/white. Selected pixels become bright pink and the
// rest is dimmed: single pixels scattered through colourful static are
// otherwise impossible to see.
  function tintedMask(img, w, h) {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const x = c.getContext("2d");
    x.drawImage(img, 0, 0, w, h);
    const d = x.getImageData(0, 0, w, h);
    for (let i = 0; i < d.data.length; i += 4) {
      // selected pixels bright pink; everything else dimmed so they stand out
      if (d.data[i] > 127) { d.data[i] = 255; d.data[i + 1] = 60; d.data[i + 2] = 210; d.data[i + 3] = 255; }
      else { d.data[i] = 10; d.data[i + 1] = 8; d.data[i + 2] = 20; d.data[i + 3] = 205; }
    }
    x.putImageData(d, 0, 0);
    return c;
  }

  function drawTone(ctx, maps, thr) {
    const peak = maxAbs(maps.tone.flat());
    if (!state.mask || peak < thr) return;
    if (!state.tinted) state.tinted = tintedMask(state.mask, maps.size[0], maps.size[1]);
    ctx.drawImage(state.tinted, 0, 0);
  }

  function toneChart(maps, thr) {
    const W = 480, H = 90, bw = W / 48, mid = H / 2;
    const top = Math.max(thr * 1.5, maxAbs(maps.tone.flat()));
    const colours = ["#e5484d", "#30a46c", "#3e63dd"];
    let out = `<line x1="0" x2="${W}" y1="${mid}" y2="${mid}" class="axis"/>`;
    maps.tone.forEach((row, c) => row.forEach((z, b) => {
      const x = (c * 16 + b) * bw, hgt = Math.min(1, Math.abs(z) / top) * (mid - 4);
      const y = z > 0 ? mid - hgt : mid;
      const strong = Math.abs(z) >= thr;
      out += `<rect x="${x + 1}" y="${y}" width="${bw - 2}" height="${hgt}" fill="${colours[c]}" opacity="${strong ? 1 : .35}">` +
             `<title>${["red", "green", "blue"][c]} ${b * 16}–${b * 16 + 15}: ${z > 0 ? "+" : ""}${z.toFixed(1)}σ</title></rect>`;
    }));
    const ty = (thr / top) * (mid - 4);
    out += `<line x1="0" x2="${W}" y1="${mid - ty}" y2="${mid - ty}" class="thr"/><line x1="0" x2="${W}" y1="${mid + ty}" y2="${mid + ty}" class="thr"/>`;
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="tone-chart" role="img" aria-label="Colour histogram deviation from static, red, green and blue">${out}</svg>` +
           `<div class="tone-legend"><span>red</span><span>green</span><span>blue</span><span class="muted">each bar: one 16-value range, dark → light; above/below the line = more/fewer pixels than static</span></div>`;
  }

  function render() {
    if (!state || !state.maps) return;
    const { maps, layer } = state;
    const cv = $("d-overlay");
    cv.width = maps.size[0]; cv.height = maps.size[1];
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, cv.width, cv.height);
    const on = $("d-ov-toggle").checked;
    cv.hidden = !on;
    const thr = state.thr[layer];
    $("d-ov-thr").value = thr;
    $("d-ov-thr-val").textContent = `${thr.toFixed(1)}σ`;
    let note = LAYERS[layer].hint;
    if (thr < (maps.defaults?.[layer] ?? STATIC_LIMIT[layer]))
      note += `. Threshold lowered to ${thr.toFixed(1)}σ so this frame's strongest evidence shows; static itself often reaches ~${STATIC_LIMIT[layer]}σ here, so expect some speckle.`;
    $("d-ov-extra").innerHTML = "";
    if (on) {
      if (layer === "mean") drawCells(ctx, maps, maps.mean, thr, false);
      if (layer === "contrast") drawCells(ctx, maps, maps.contrast, thr, true);
      if (layer === "rows") drawRows(ctx, maps, thr);
      if (layer === "geometry") {
        const missing = drawGeometry(ctx, maps, thr);
        if (missing.length) note += `. Below threshold: ${missing.join(", ")}.`;
      }
      if (layer === "tone") {
        drawTone(ctx, maps, thr);
        $("d-ov-extra").innerHTML = toneChart(maps, thr);
        if (!state.mask) note += ". (No pixel mask stored for this frame.)";
      }
    }
    $("d-ov-note").textContent = note;
    $("d-ov-key").innerHTML = (KEYS[layer] || []).map(([c, t]) =>
      `<span><i style="background:${c}"></i>${t}</span>`).join("");
    $("d-ov-reset").disabled = thr === defaultThreshold(layer, maps);
    document.querySelectorAll("#d-ov-layers button").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.layer === layer)));
  }

  async function show(item) {
    state = { item, maps: null, mask: null, layer: null, thr: {} };
    const box = $("d-ov");
    box.hidden = !item.maps;
    $("d-overlay").hidden = true;
    if (!item.maps) return;
    try {
      const maps = await (await fetch(item.maps)).json();
      if (!state || state.item !== item) return;
      state.maps = maps;
      for (const k of Object.keys(LAYERS)) state.thr[k] = defaultThreshold(k, maps);
      state.layer = DEFAULT_LAYER[item.reason] || "mean";
      if (item.mask) {
        const img = new Image();
        img.onload = () => { if (state && state.item === item) { state.mask = img; render(); } };
        img.src = item.mask;
      }
      $("d-ov-toggle").checked = true;
      render();
    } catch {
      box.hidden = true;
    }
  }

  function setLayer(k) { if (state && state.maps && LAYERS[k]) { state.layer = k; render(); } }

  function init(testName) {
    if (testName) nameOf = testName;
    $("d-ov-reset").onclick = () => {
      if (state && state.maps) { state.thr[state.layer] = defaultThreshold(state.layer, state.maps); render(); }
    };
    const bar = $("d-ov-layers");
    bar.replaceChildren(...Object.entries(LAYERS).map(([k, v]) => {
      const b = document.createElement("button");
      b.type = "button"; b.dataset.layer = k; b.textContent = v.label;
      b.title = `${v.hint} (tests: ${v.tests.map((t) => nameOf(t)).join(", ")})`;
      b.onclick = () => { if (state) { state.layer = k; render(); } };
      return b;
    }));
    $("d-ov-thr").oninput = (e) => { if (state && state.layer) { state.thr[state.layer] = +e.target.value; render(); } };
    $("d-ov-toggle").onchange = render;
  }

  return {
    init, show, reset: () => { state = null; },
    toggle: () => { if (state && state.maps) { $("d-ov-toggle").checked = !$("d-ov-toggle").checked; render(); } },
    setLayerIndex: (i) => setLayer(Object.keys(LAYERS)[i]),
  };
})();
