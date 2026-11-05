// Showcase page: the build story, the live-coding demo and live repo activity.
(() => {
  const $ = (id) => document.getElementById(id);
  const REPO = "Ow-Hill/Surfing-Babel_Claude-Code-Test";
  const API = `https://api.github.com/repos/${REPO}`;
  const GH = `https://github.com/${REPO}`;
  const TOKEN_KEY = "surfing-babel-token";          // shared with the scanner's Scan now button
  const START_KEY = "surfing-babel-demo-start";
  const params = new URLSearchParams(location.search);
  if (params.get("present") === "1") document.body.classList.add("present");

  const el = (tag, props = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else e.setAttribute(k, v);
    }
    e.append(...kids.filter((k) => k !== null && k !== undefined && k !== false));
    return e;
  };
  const ago = (iso) => {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  };
  const mmss = (ms) => { const s = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
    del: (k) => { try { localStorage.removeItem(k); } catch {} },
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
      li.append(el("div", { class: "tl-head" },
        el("span", { class: "tl-date mono", text: ev.date }),
        ev.pr ? el("a", { href: `${GH}/pull/${ev.pr}`, class: "tl-pr mono", text: `PR #${ev.pr}` }) : null),
      el("h3", { text: ev.title }));
      if (ev.request) li.append(el("p", { class: "tl-req" }, el("b", { text: "Request: " }), ev.request));
      li.append(el("p", { text: ev.did }));
      for (const m of ev.moments || []) {
        li.append(el("p", { class: `moment ${m.kind.replace(/\s+/g, "-")}` },
          el("span", { class: "tag", text: m.kind }), " ", m.text));
      }
      return li;
    }));
  }

  // ------------------------------------------------------------ the slot: spec checklist + result
  const PROMPTS = {
    stub: "Implement find_of_the_day in babel_surfer/demo_slot.py. The module docstring and " +
          "tests/test_demo_slot.py are the spec. Run the tests until they pass, run the full test suite, " +
          "then commit, open a pull request to main, and merge it once its checks are green. " +
          "Keep the change to that one file.",
    buggy: "find_of_the_day in babel_surfer/demo_slot.py runs, but some of its spec tests fail. " +
           "Run tests/test_demo_slot.py, explain which requirements are broken and why, fix them, " +
           "run the full test suite, then commit, open a pull request to main, and merge it once its " +
           "checks are green. Keep the change to that one file.",
  };
  let slot = { spec: null, fotd: null };

  async function loadSlot() {
    const get = async (p) => { try { return await (await fetch(p, { cache: "no-cache" })).json(); } catch { return null; } };
    const [spec, fotd] = await Promise.all([get("agent/spec_results.json"), get("agent/find_of_the_day.json")]);
    slot = { spec, fotd };
    renderSlot();
  }

  function renderSlot() {
    const { spec, fotd } = slot;
    const state = $("slot-state");
    const variant = spec ? spec.variant : null;
    const label = !spec ? ["not built yet", "near"]
      : spec.status === "waiting" ? ["waiting for the agent", "near"]
      : spec.status === "passed" ? ["implemented: all checks pass", "clear"]
      : spec.status === "error" ? ["checks couldn't run", "striking"]
      : [`${spec.passed} of ${spec.total} checks pass`, "striking"];
    state.textContent = label[0];
    state.className = `tag ${label[1]}`;
    $("spec-meta").textContent = spec
      ? `(${variant === "stub" ? "unimplemented" : variant === "buggy" ? "buggy version" : "the agent's version"}` +
        `${spec.built_at ? `, checked ${ago(spec.built_at)}` : ""})` : "";

    const icon = { passed: "✓", failed: "✗", waiting: "○", missing: "?" };
    $("checklist").replaceChildren(...(spec && spec.requirements.length ? spec.requirements : []).map((r) =>
      el("li", { class: `req ${r.status}` },
        el("span", { class: "req-icon", "aria-hidden": "true", text: icon[r.status] || "?" }),
        el("span", {}, r.requirement,
          r.status === "failed" && r.message ? el("span", { class: "req-msg mono", text: r.message }) : null))));
    if (spec && spec.status === "error") $("checklist").append(el("li", { class: "req failed", text: spec.message }));

    // the result itself, once it is implemented
    const box = $("fotd-box");
    box.replaceChildren();
    if (fotd && fotd.status === "ok" && fotd.pick) {
      const p = fotd.pick;
      box.append(el("a", { class: "fotd", href: `./#${p.location}` },
        p.image ? el("img", { src: `data/${p.image}`, alt: p.caption }) : null,
        el("div", {}, el("p", { class: "fotd-cap", text: p.caption }),
          el("p", { class: "muted small", text: `Find of the day, ${fotd.day} · open it on the scanner →` }))));
    } else if (fotd && fotd.status === "no_finds") {
      box.append(el("p", { class: "muted small", text: "Implemented, but there are no finds to feature yet." }));
    } else if (fotd && fotd.status === "error") {
      box.append(el("p", { class: "error", text: `The implementation raised an error: ${fotd.message}` }));
    }

    $("prompt").textContent = variant === "buggy" ? PROMPTS.buggy
      : variant === "stub" || !variant ? PROMPTS.stub
      : "The slot already holds an implementation. Reset it (Presenter tools) to run the demo again.";
  }

  // ------------------------------------------------------------ GitHub API (conditional requests)
  // A 304 doesn't count against the 60-requests-per-hour limit for
  // unauthenticated visitors, so live mode can poll every 15 s.
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

  // ------------------------------------------------------------ demo progress
  const STEPS = [
    ["start", "Prompt given"],
    ["pr", "Pull request opened"],
    ["ci", "Tests pass on the PR"],
    ["merged", "Merged to main"],
    ["publish", "Building and publishing"],
    ["live", "Live on this page"],
  ];
  let startedAt = +store.get(START_KEY) || null;
  if (startedAt && Date.now() - startedAt > 3 * 3600 * 1000) { startedAt = null; store.del(START_KEY); }  // stale demo
  let finishedAt = null;
  let demo = {};           // step -> { state: "todo"|"doing"|"done"|"fail", note, href }

  function renderStepper() {
    $("stepper").replaceChildren(...STEPS.map(([k, label], i) => {
      const s = demo[k] || { state: "todo" };
      const content = s.href ? el("a", { href: s.href, target: "_blank", rel: "noopener", text: label }) : el("span", { text: label });
      return el("li", { class: `step ${s.state}` },
        el("span", { class: "step-dot", "aria-hidden": "true", text: s.state === "done" ? "✓" : s.state === "fail" ? "✗" : String(i + 1) }),
        el("span", { class: "step-body" }, content, s.note ? el("small", { class: "muted", text: s.note }) : null));
    }));
    $("start").textContent = startedAt ? "Restart the clock" : "Start the clock";
  }

  function tick() {
    $("clock").textContent = startedAt ? mmss((finishedAt || Date.now()) - startedAt) : "0:00";
    $("clock").classList.toggle("done", !!finishedAt);
  }
  setInterval(tick, 1000);

  async function updateDemo(prs, runs) {
    demo = {};
    finishedAt = null;
    if (!startedAt) { renderStepper(); return; }
    demo.start = { state: "done", note: `at ${new Date(startedAt).toLocaleTimeString()}` };
    const since = startedAt - 60 * 1000;                       // allow for clock skew
    const pr = prs.filter((p) => new Date(p.created_at).getTime() >= since)
                  .sort((a, b) => new Date(a.created_at) - new Date(b.created_at))[0];
    if (!pr) { demo.pr = { state: "doing", note: "waiting for the agent…" }; renderStepper(); return; }
    demo.pr = { state: "done", note: `#${pr.number} ${pr.title}`, href: pr.html_url };

    // CI on the PR's head commit
    try {
      const checks = await gh(`/commits/${pr.head.sha}/check-runs?per_page=20`);
      const ci = (checks.check_runs || []).filter((c) => c.name === "pytest");
      const run = ci.sort((a, b) => new Date(b.started_at) - new Date(a.started_at))[0];
      demo.ci = !run ? { state: "doing", note: "waiting for the Tests workflow…" }
        : run.status !== "completed" ? { state: "doing", note: "running…", href: run.html_url }
        : run.conclusion === "success" ? { state: "done", note: "all green", href: run.html_url }
        : { state: "fail", note: `${run.conclusion}: the agent should fix this`, href: run.html_url };
    } catch { demo.ci = { state: "doing", note: "couldn't check" }; }

    if (!pr.merged_at) {
      demo.merged = { state: pr.state === "closed" ? "fail" : "todo", note: pr.state === "closed" ? "closed without merging" : "" };
      renderStepper(); return;
    }
    demo.merged = { state: "done", note: `at ${new Date(pr.merged_at).toLocaleTimeString()}` };
    const merged = new Date(pr.merged_at).getTime();
    const publish = (runs.workflow_runs || []).filter((r) => r.name.startsWith("Scan") &&
      (r.head_sha === pr.merge_commit_sha || new Date(r.created_at).getTime() >= merged - 5000))
      .sort((a, b) => new Date(a.created_at) - new Date(b.created_at))[0];
    const deploy = (runs.workflow_runs || []).filter((r) => r.name.startsWith("pages") &&
      new Date(r.created_at).getTime() >= merged).sort((a, b) => new Date(b.created_at) - new Date(a.created_at))[0];
    demo.publish = !publish ? { state: "doing", note: "waiting for the publish run…" }
      : publish.status !== "completed" ? { state: "doing", note: publish.status.replace("_", " "), href: publish.html_url }
      : publish.conclusion !== "success" ? { state: "fail", note: publish.conclusion, href: publish.html_url }
      : !deploy || deploy.status !== "completed" ? { state: "doing", note: "deploying to GitHub Pages…", href: publish.html_url }
      : deploy.conclusion === "success" ? { state: "done", note: "deployed", href: deploy.html_url }
      : { state: "fail", note: `Pages deploy ${deploy.conclusion}`, href: deploy.html_url };

    const builtAt = slot.spec && slot.spec.built_at ? new Date(slot.spec.built_at).getTime() : 0;
    if (builtAt >= merged - 5000 && slot.spec.status === "passed") {
      demo.live = { state: "done", note: "all checks pass on the live site" };
      finishedAt = Math.max(builtAt, merged);
    } else if (demo.publish.state === "done") {
      demo.live = { state: "doing", note: "waiting for the new page (browser cache can take a minute)…" };
    }
    renderStepper();
  }

  $("start").onclick = () => {
    startedAt = Date.now();
    store.set(START_KEY, String(startedAt));
    finishedAt = null;
    $("live-mode").checked = true;
    refresh();
  };
  $("copy-prompt").onclick = async () => {
    try { await navigator.clipboard.writeText($("prompt").textContent); $("copy-prompt").textContent = "Copied"; }
    catch { $("copy-prompt").textContent = "Select and copy it manually"; }
    setTimeout(() => ($("copy-prompt").textContent = "Copy prompt"), 1500);
  };

  // ------------------------------------------------------------ reset (presenter)
  $("reset-link").href = `${GH}/actions/workflows/demo-reset.yml`;
  document.querySelectorAll("button.reset").forEach((b) => (b.onclick = async () => {
    const variant = b.dataset.variant;
    const token = store.get(TOKEN_KEY);
    if (!token) {
      $("reset-status").textContent = "No GitHub token saved in this browser: opening the workflow on GitHub instead (choose the variant there).";
      window.open(`${GH}/actions/workflows/demo-reset.yml`, "_blank", "noopener");
      return;
    }
    $("reset-status").textContent = "Asking GitHub to reset the slot…";
    try {
      const r = await fetch(`${API}/actions/workflows/demo-reset.yml/dispatches`, {
        method: "POST",
        headers: { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}`, "X-GitHub-Api-Version": "2022-11-28" },
        body: JSON.stringify({ ref: "main", inputs: { variant } }),
      });
      if (r.status !== 204) throw new Error(`GitHub ${r.status}`);
      startedAt = null; store.del(START_KEY); finishedAt = null;
      $("reset-status").textContent = `Reset to "${variant === "stub" ? "unimplemented" : "buggy"}" requested. ` +
        "The page shows it in about 3 minutes (reset, then publish).";
      $("live-mode").checked = true;
      refresh();
    } catch (e) {
      $("reset-status").textContent = `GitHub refused (${e.message}). The token needs "Actions: Read and write" on ${REPO}; or use the workflow link above.`;
    }
  }));

  // ------------------------------------------------------------ live activity
  const item = (href, title, meta, chip) => el("li", {},
    el("a", { href, target: "_blank", rel: "noopener", text: title }),
    el("div", { class: "muted small" }, chip ? el("span", { class: `tag ${chip[1]}`, text: chip[0] }) : null, chip ? " " : "", meta));

  let timer = null;
  async function refresh() {
    try {
      const [prs, commits, runs] = await Promise.all([
        gh("/pulls?state=all&per_page=6&sort=created&direction=desc"),
        gh("/commits?sha=main&per_page=6"),
        gh("/actions/runs?per_page=10"),
      ]);
      $("act-prs").replaceChildren(...prs.map((p) => item(p.html_url, `#${p.number} ${p.title}`, ago(p.updated_at),
        p.merged_at ? ["merged", "clear"] : p.state === "open" ? ["open", "faint"] : ["closed", "static"])));
      $("act-commits").replaceChildren(...commits.map((c) => item(c.html_url,
        c.commit.message.split("\n")[0], `${c.sha.slice(0, 7)} · ${ago(c.commit.author.date)}`)));
      $("act-runs").replaceChildren(...runs.workflow_runs.slice(0, 6).map((r) => item(r.html_url,
        r.name.startsWith("pages") ? "Deploy to GitHub Pages" : r.name.startsWith("Scan") ? `Scan & publish (${r.event.replace("_", " ")})` : r.name,
        ago(r.created_at),
        r.status !== "completed" ? [r.status.replace("_", " "), "faint"]
          : r.conclusion === "success" ? ["success", "clear"] : [r.conclusion || "done", "striking"])));
      $("act-status").textContent = `updated ${new Date().toLocaleTimeString()}`;
      await loadSlot();
      await updateDemo(prs, runs);
    } catch (e) {
      $("act-status").textContent = e.reset
        ? `GitHub's hourly limit for this browser was reached; resumes ${new Date(e.reset).toLocaleTimeString()}`
        : "couldn't reach GitHub; will retry";
      renderStepper();
    }
    clearTimeout(timer);
    if ($("live-mode").checked && !document.hidden) timer = setTimeout(refresh, 15000);
  }

  $("live-mode").onchange = () => refresh();
  document.addEventListener("visibilitychange", () => { if (!document.hidden && $("live-mode").checked) refresh(); });
  if (params.get("live") === "1" || params.get("present") === "1" || startedAt) $("live-mode").checked = true;

  loadStory();
  loadSlot();
  renderStepper();
  tick();
  refresh();
})();
