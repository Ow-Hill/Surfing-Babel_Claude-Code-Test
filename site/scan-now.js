// "Scan now": starts the GitHub Actions scan and tracks it live.
//
// GitHub Pages is static, and Babelia sends no CORS headers (a browser can't
// read its pixels), so scans run in GitHub Actions. Starting one needs a token
// with Actions write access. The owner pastes a fine-grained token once; it
// is kept only in this browser's localStorage and sent only to api.github.com.
// Anyone can see scan status: that uses GitHub's public, unauthenticated API.

(() => {
  const REPO = "Ow-Hill/Surfing-Babel_Claude-Code-Test";
  const WORKFLOW = "scan-and-publish.yml";
  const API = `https://api.github.com/repos/${REPO}`;
  const RUNS_PAGE = `https://github.com/${REPO}/actions/workflows/${WORKFLOW}`;
  const TOKEN_KEY = "surfing-babel-token";
  const $ = (id) => document.getElementById(id);

  const store = {
    get() { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } },
    set(v) { try { localStorage.setItem(TOKEN_KEY, v); } catch {} },
    clear() { try { localStorage.removeItem(TOKEN_KEY); } catch {} },
  };

  let pollTimer = null;
  let watching = false;          // true while a run we know about is active
  let lastUpdated = null;        // stats.json updated_at, to spot new data

  const fmtAgo = (iso) => {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 90) return "just now";
    if (s < 5400) return `${Math.round(s / 60)} min ago`;
    return `${Math.round(s / 3600)} h ago`;
  };

  function setStatus(text, state) {
    const el = $("scan-status");
    el.textContent = text;
    el.dataset.state = state || "";
  }

  async function latestRuns() {
    const r = await fetch(`${API}/actions/workflows/${WORKFLOW}/runs?per_page=5`, {
      headers: { Accept: "application/vnd.github+json" }, cache: "no-store",
    });
    if (!r.ok) throw new Error(`GitHub API ${r.status}`);
    return (await r.json()).workflow_runs || [];
  }

  async function refreshStatus() {
    let runs;
    try { runs = await latestRuns(); } catch (e) {
      setStatus("scan status unavailable", "idle");
      return schedule(120000);
    }
    const active = runs.find((r) => r.status !== "completed");
    if (active) {
      const label = active.status === "in_progress"
        ? `scanning: started ${fmtAgo(active.run_started_at || active.created_at)}`
        : "scan queued";
      setStatus(label, "running");
      $("scan-btn").disabled = true;
      watching = true;
      return schedule(30000);
    }
    $("scan-btn").disabled = false;
    const last = runs[0];
    if (watching && last) {
      watching = false;
      if (last.conclusion === "success") {
        setStatus("scan finished: publishing results…", "done");
        waitForNewData();
      } else {
        setStatus(`last scan ${last.conclusion}`, "error");
      }
    } else if (last) {
      setStatus(`last scan ${fmtAgo(last.updated_at)}`, "idle");
    }
    schedule(document.hidden ? 300000 : 120000);
  }

  function schedule(ms) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(refreshStatus, ms);
  }

  // After a run, Pages takes a minute or two to redeploy; reload the page
  // data as soon as stats.json changes.
  async function waitForNewData(tries = 0) {
    try {
      const s = await (await fetch("data/stats.json", { cache: "no-store" })).json();
      if (lastUpdated !== null && s.updated_at !== lastUpdated) {
        lastUpdated = s.updated_at;
        setStatus("new results loaded", "done");
        if (window.reloadData) window.reloadData();
        return;
      }
      lastUpdated = s.updated_at;
    } catch {}
    if (tries < 20) setTimeout(() => waitForNewData(tries + 1), 20000);
  }

  async function dispatch(token, minutes) {
    const r = await fetch(`${API}/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref: "main", inputs: { minutes: String(minutes) } }),
    });
    if (r.status === 204) return;
    let msg = `${r.status}`;
    try { msg += `: ${(await r.json()).message}`; } catch {}
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }

  function openDialog() {
    const hasToken = !!store.get();
    $("scan-token-row").hidden = hasToken;
    $("scan-forget").hidden = !hasToken;
    $("scan-error").hidden = true;
    $("scan-dialog").showModal();
  }

  async function start(ev) {
    ev.preventDefault();
    const minutes = parseInt($("scan-minutes").value, 10);
    const typed = $("scan-token").value.trim();
    const token = typed || store.get();
    if (!token) {
      showError("Paste a token first, or start the scan on GitHub instead.");
      return;
    }
    $("scan-go").disabled = true;
    try {
      await dispatch(token, minutes);
      if (typed && $("scan-remember").checked) store.set(typed);
      $("scan-token").value = "";
      $("scan-dialog").close();
      setStatus("scan requested…", "running");
      $("scan-btn").disabled = true;
      watching = true;
      try {
        const s = await (await fetch("data/stats.json", { cache: "no-store" })).json();
        lastUpdated = s.updated_at;
      } catch {}
      schedule(8000);
    } catch (e) {
      if (e.status === 401 || e.status === 403 || e.status === 404) {
        if (!typed) store.clear();
        showError(`GitHub refused the token (${e.message}). It needs "Actions: Read and write" on ${REPO}.`);
        $("scan-token-row").hidden = false;
      } else {
        showError(`Couldn't start the scan: ${e.message}`);
      }
    } finally {
      $("scan-go").disabled = false;
    }
  }

  function showError(text) {
    $("scan-error").textContent = text;
    $("scan-error").hidden = false;
  }

  $("scan-btn").addEventListener("click", openDialog);
  $("scan-form").addEventListener("submit", start);
  $("scan-cancel").addEventListener("click", () => $("scan-dialog").close());
  $("scan-forget").addEventListener("click", () => {
    store.clear();
    $("scan-forget").hidden = true;
    $("scan-token-row").hidden = false;
  });
  $("scan-github").href = RUNS_PAGE;
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshStatus(); });
  refreshStatus();
})();
