// Showcase page: the build story, the live-coding slot and live repo activity.
(() => {
  const $ = (id) => document.getElementById(id);
  const REPO = "Ow-Hill/Surfing-Babel_Claude-Code-Test";
  const API = `https://api.github.com/repos/${REPO}`;
  const GH = `https://github.com/${REPO}`;

  const el = (tag, props = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else e.setAttribute(k, v);
    }
    e.append(...kids.filter(Boolean));
    return e;
  };
  const ago = (iso) => {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  };

  // ------------------------------------------------------------ story
  async function loadStory() {
    let t;
    try { t = await (await fetch("agent-timeline.json", { cache: "no-cache" })).json(); }
    catch { $("intro").textContent = "Couldn't load the project story. Try refreshing."; return; }
    $("intro").textContent = t.intro;
    $("a-stats").replaceChildren(...t.stats.map((s) =>
      el("div", { class: "stat" }, el("b", { text: s.value }), el("small", { text: s.label }))));
    $("timeline").replaceChildren(...t.events.map((ev) => {
      const li = el("li", { class: "tl-item" });
      const head = el("div", { class: "tl-head" },
        el("span", { class: "tl-date mono", text: ev.date }),
        ev.pr ? el("a", { href: `${GH}/pull/${ev.pr}`, class: "tl-pr mono", text: `PR #${ev.pr}` }) : null);
      li.append(head, el("h3", { text: ev.title }));
      if (ev.request) li.append(el("p", { class: "tl-req" }, el("b", { text: "Request: " }), ev.request));
      li.append(el("p", { text: ev.did }));
      for (const m of ev.moments || []) {
        li.append(el("p", { class: `moment ${m.kind.replace(/\s+/g, "-")}` },
          el("span", { class: "tag", text: m.kind }), " ", m.text));
      }
      return li;
    }));
  }

  // ------------------------------------------------------------ live-coding slot
  const SPEC = [
    "Return nothing when there are no finds.",
    "Same finds + same day → same pick, whatever order the finds are in.",
    "Different days feature different finds (at least 3 over 30 days).",
    "Stronger finds are featured more often.",
    "A one-line caption (≤ 140 characters) that includes the score, e.g. “5.8σ”.",
  ];

  async function loadSlot() {
    let r;
    try { r = await (await fetch("agent/find_of_the_day.json", { cache: "no-cache" })).json(); }
    catch { r = { status: "not_published" }; }
    const state = $("slot-state"), body = $("slot-body");
    const specList = el("ul", { class: "spec" }, ...SPEC.map((s) => el("li", { text: s })));
    const signature = el("pre", { class: "code" },
      "# babel_surfer/demo_slot.py\ndef find_of_the_day(finds: list[dict], day: date) -> dict | None:\n    raise NotImplementedError");
    if (r.status === "ok") {
      state.textContent = "implemented"; state.className = "tag clear";
      const p = r.pick;
      const card = el("a", { class: "fotd", href: `./#${p.location}` },
        p.image ? el("img", { src: `data/${p.image}`, alt: p.caption }) : null,
        el("div", {}, el("p", { class: "fotd-cap", text: p.caption }),
          el("p", { class: "muted small", text: `Featured on ${r.day} · ${p.tier} · open it on the scanner →` })));
      body.replaceChildren(card,
        el("details", {}, el("summary", { text: "The spec the agent implemented" }), specList));
    } else {
      const label = { not_implemented: "waiting for the agent", error: "implementation error",
                      no_finds: "implemented, no finds yet", not_published: "not built yet" }[r.status] || r.status;
      state.textContent = label;
      state.className = `tag ${r.status === "error" ? "striking" : "near"}`;
      body.replaceChildren(...[
        el("p", { text: "A deliberately small task kept for live demos: during a talk, Claude Code is asked to implement this one function from its docstring and tests alone. It then runs the tests, opens and merges a pull request, and the result appears right here, usually within minutes." }),
        r.status === "error" ? el("p", { class: "error", text: `The current implementation failed: ${r.message}` }) : null,
        signature, el("h3", { text: "What it must do" }), specList,
        el("p", { class: "muted small" }, "Spec: ",
          el("a", { href: `${GH}/blob/main/babel_surfer/demo_slot.py`, text: "demo_slot.py" }), " · tests: ",
          el("a", { href: `${GH}/blob/main/tests/test_demo_slot.py`, text: "test_demo_slot.py" }))].filter(Boolean));
    }
  }

  // ------------------------------------------------------------ live activity
  // Conditional requests (ETag): a 304 doesn't count against GitHub's
  // 60-requests-per-hour limit for unauthenticated visitors, so live mode
  // can poll every 15 s.
  const cache = new Map();
  async function gh(path) {
    const url = `${API}${path}`;
    const hit = cache.get(url);
    const r = await fetch(url, { headers: { Accept: "application/vnd.github+json", ...(hit ? { "If-None-Match": hit.etag } : {}) } });
    if (r.status === 304 && hit) return hit.data;
    if (!r.ok) {
      const err = new Error(`GitHub ${r.status}`);
      if (r.headers.get("x-ratelimit-remaining") === "0") err.reset = +r.headers.get("x-ratelimit-reset") * 1000;
      throw err;
    }
    const data = await r.json();
    cache.set(url, { etag: r.headers.get("etag"), data });
    return data;
  }

  const item = (href, title, meta, chip) => el("li", {},
    el("a", { href, target: "_blank", rel: "noopener", text: title }),
    el("div", { class: "muted small" }, chip ? el("span", { class: `tag ${chip[1]}`, text: chip[0] }) : null, chip ? " " : "", meta));

  let timer = null;
  async function refresh() {
    try {
      const [prs, commits, runs] = await Promise.all([
        gh("/pulls?state=all&per_page=6&sort=updated&direction=desc"),
        gh("/commits?sha=main&per_page=6"),
        gh("/actions/runs?per_page=6"),
      ]);
      $("act-prs").replaceChildren(...prs.map((p) => item(p.html_url, `#${p.number} ${p.title}`, ago(p.updated_at),
        p.merged_at ? ["merged", "clear"] : p.state === "open" ? ["open", "faint"] : ["closed", "static"])));
      $("act-commits").replaceChildren(...commits.map((c) => item(c.html_url,
        c.commit.message.split("\n")[0], `${c.sha.slice(0, 7)} · ${ago(c.commit.author.date)}`)));
      $("act-runs").replaceChildren(...runs.workflow_runs.map((r) => item(r.html_url,
        r.name.startsWith("pages") ? "Deploy to GitHub Pages" : `Scan (${r.event.replace("_", " ")})`,
        ago(r.created_at),
        r.status !== "completed" ? [r.status.replace("_", " "), "faint"]
          : r.conclusion === "success" ? ["success", "clear"] : [r.conclusion || "done", "striking"])));
      $("act-status").textContent = `updated ${new Date().toLocaleTimeString()}`;
    } catch (e) {
      $("act-status").textContent = e.reset
        ? `GitHub's hourly limit for this browser was reached; resumes ${new Date(e.reset).toLocaleTimeString()}`
        : "couldn't reach GitHub; will retry";
    }
    clearTimeout(timer);
    if ($("live-mode").checked && !document.hidden) timer = setTimeout(() => { refresh(); loadSlot(); }, 15000);
  }

  $("live-mode").onchange = () => { refresh(); };
  document.addEventListener("visibilitychange", () => { if (!document.hidden && $("live-mode").checked) refresh(); });
  // ?live=1 turns live mode on (handy for a presenter's screen)
  if (new URLSearchParams(location.search).get("live") === "1") $("live-mode").checked = true;

  loadStory();
  loadSlot();
  refresh();
})();
