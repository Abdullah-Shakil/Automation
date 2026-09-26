/* Leadlane desk — sort, theme, Find-style profiles */
(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const sortState = {};
  let profileList = [];
  let profileIndex = 0;
  let openProfileId = null;

  function escapeHtml(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function cmpValues(a, b, dir) {
    const mul = dir === "asc" ? 1 : -1;
    if (a == null && b == null) return 0;
    if (a == null || a === "") return 1;
    if (b == null || b === "") return -1;
    const an = Number(a);
    const bn = Number(b);
    if (!Number.isNaN(an) && !Number.isNaN(bn) && String(a).trim() !== "" && String(b).trim() !== "") {
      return (an - bn) * mul;
    }
    const as = String(a).toLowerCase();
    const bs = String(b).toLowerCase();
    if (as < bs) return -1 * mul;
    if (as > bs) return 1 * mul;
    return 0;
  }

  function syncSortHeaders(tableKey) {
    const state = sortState[tableKey];
    if (!state) return;
    $$(`table[data-table="${tableKey}"] .th-sort`).forEach((btn) => {
      const active = btn.dataset.sort === state.key;
      btn.classList.toggle("is-active", active);
      btn.dataset.dir = active ? state.dir : "";
      btn.setAttribute("aria-sort", active ? (state.dir === "asc" ? "ascending" : "descending") : "none");
      const ind = btn.querySelector(".sort-ind");
      if (ind) ind.textContent = active ? (state.dir === "asc" ? "↑" : "↓") : "";
    });
  }

  function sortTableDom(table) {
    const tableKey = table.dataset.table;
    if (!tableKey || !sortState[tableKey]) return;
    const { key, dir } = sortState[tableKey];
    const tbody = table.tBodies[0];
    if (!tbody) return;
    const rows = [...tbody.querySelectorAll("tr")].filter((r) => r.getAttribute(`data-sort-${key}`) != null);
    if (!rows.length) return;
    rows.sort((a, b) => {
      const av = a.getAttribute(`data-sort-${key}`) ?? "";
      const bv = b.getAttribute(`data-sort-${key}`) ?? "";
      return cmpValues(av, bv, dir);
    });
    rows.forEach((r) => tbody.appendChild(r));
  }

  function setSort(tableKey, key) {
    if (!sortState[tableKey]) sortState[tableKey] = { key, dir: "asc" };
    const cur = sortState[tableKey];
    if (cur.key === key) {
      cur.dir = cur.dir === "asc" ? "desc" : "asc";
    } else {
      cur.key = key;
      const highFirst = new Set(["usage", "leads", "found", "connected"]);
      cur.dir = highFirst.has(key) ? "desc" : "asc";
    }
    syncSortHeaders(tableKey);
    const table = $(`table[data-table="${tableKey}"]`);
    if (table) sortTableDom(table);
  }

  function wireSortableTables() {
    $$(".sortable-table").forEach((table) => {
      const key = table.dataset.table;
      if (!key) return;
      if (!sortState[key]) {
        const active = table.querySelector(".th-sort.is-active");
        sortState[key] = {
          key: active?.dataset.sort || "name",
          dir: active?.dataset.dir || "asc",
        };
      }
      syncSortHeaders(key);
    });
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest?.(".sortable-table .th-sort");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const table = btn.closest("table")?.dataset.table;
    if (table && btn.dataset.sort) setSort(table, btn.dataset.sort);
  });

  function applyTheme(theme) {
    const next = theme === "night" ? "night" : "day";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("leadlane-theme", next);
    } catch (e) {}
    const label = $("#theme-toggle .theme-label");
    if (label) label.textContent = next === "night" ? "Day" : "Night";
    const toggle = $("#theme-toggle");
    if (toggle) toggle.setAttribute("aria-label", `Switch to ${next === "night" ? "day" : "night"} theme`);
  }

  function initTheme() {
    const current = document.documentElement.getAttribute("data-theme") || "day";
    applyTheme(current);
    $("#theme-toggle")?.addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme") || "day";
      applyTheme(cur === "night" ? "day" : "night");
    });
  }

  function loadProfiles() {
    const el = $("#profile-data");
    if (!el) {
      profileList = [];
      return;
    }
    try {
      const data = JSON.parse(el.textContent || "{}");
      const bots = data.bots || [];
      const workers = data.workers || [];
      const schedulers = data.schedulers || [];
      const params = new URLSearchParams(window.location.search);
      const tab = params.get("tab") || "workers";
      if (tab === "workers") profileList = [...workers, ...schedulers];
      else if (tab === "bots") profileList = bots;
      else profileList = [...bots, ...workers, ...schedulers];
    } catch (e) {
      profileList = [];
    }
  }

  function statusClass(status) {
    const s = String(status || "").toLowerCase();
    if (s === "running" || s === "ok" || s === "live" || s === "on" || s === "active") return "running";
    if (s === "paused" || s === "limit_reached" || s === "key" || s === "idle") return "paused";
    if (s === "error" || s === "fail" || s === "off" || s === "stopped") return "error";
    return "";
  }

  function kindLabel(b) {
    if (b.kind === "worker") return "Worker profile";
    if (b.kind === "scheduler") return "Scheduler profile";
    if (b.kind === "api") return "API profile";
    return "Bot profile";
  }

  function linkBtn(href, label) {
    if (!href) return "";
    return `<a class="button" href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`;
  }

  function renderProfileSheet() {
    const b = profileList[profileIndex];
    if (!b) return;
    openProfileId = b.id || null;
    $("#profile-index").textContent = `${profileIndex + 1} / ${profileList.length}`;
    const st = statusClass(b.status);
    let usageHtml = `<p class="usage-none">${escapeHtml(b.quota_title || b.free_tier || "No usage cap")}</p>`;
    if (b.limit != null && b.used != null) {
      const pct = b.pct_used == null ? 0 : Math.max(4, b.pct_used);
      const barClass = b.pct_used >= 85 ? "hot" : b.pct_used >= 60 ? "warn" : "";
      usageHtml = `
        <div class="usage-inline">
          <div class="bar ${barClass}"><span style="width:${pct}%"></span></div>
          <div class="usage-meta"><span>${escapeHtml(b.used)} / ${escapeHtml(b.limit)}</span></div>
          <div class="usage-reset"><span>${escapeHtml(b.quota_title || "")}</span>
          ${b.reset_in ? `<span>Resets in ${escapeHtml(b.reset_in)}</span>` : ""}</div>
        </div>`;
    }
    const links = [];
    if (b.source_url) links.push(linkBtn(b.source_url, "Website"));
    if (b.docs_url) links.push(linkBtn(b.docs_url, "Docs"));
    if (b.console_url) links.push(linkBtn(b.console_url, "Console"));
    if (b.signup_url && b.signup_url !== b.source_url) {
      links.push(linkBtn(b.signup_url, b.signup_label || "Sign up"));
    }
    if (b.kind === "bot" && b.key) {
      links.push(`<a class="button" href="/bots/${escapeHtml(b.key)}">Open live page</a>`);
    }
    $("#profile-body").innerHTML = `
      <div class="profile-hero">
        <p class="kind-tag">${kindLabel(b)}</p>
        <h2 id="profile-title">${escapeHtml(b.name)}</h2>
        <p class="role">${escapeHtml(b.role || "")}</p>
        <span class="status ${st}">${escapeHtml(b.status_label || b.conn || b.status || "")}</span>
      </div>
      <p class="profile-summary">${escapeHtml(b.profile_summary || b.description || "")}</p>
      <dl class="profile-dl">
        <div><dt>Source</dt><dd>${escapeHtml(b.source_name || "—")}</dd></div>
        <div><dt>Origin</dt><dd>${escapeHtml(b.origin || "—")}</dd></div>
        ${b.env_name ? `<div><dt>Env key</dt><dd><code>${escapeHtml(b.env_name)}</code></dd></div>` : ""}
        ${b.free_tier ? `<div><dt>Free tier</dt><dd>${escapeHtml(b.free_tier)}</dd></div>` : ""}
        ${b.leads_found != null ? `<div><dt>Leads found</dt><dd>${escapeHtml(b.leads_found)}</dd></div>` : ""}
        ${b.progress_note ? `<div><dt>Progress</dt><dd>${escapeHtml(b.progress_note)}</dd></div>` : ""}
        ${b.reason ? `<div><dt>Note</dt><dd>${escapeHtml(b.reason)}</dd></div>` : ""}
        ${b.last_error ? `<div><dt>Last error</dt><dd>${escapeHtml(b.last_error)}</dd></div>` : ""}
        ${b.how_to ? `<div><dt>How to connect</dt><dd class="pre">${escapeHtml(b.how_to)}</dd></div>` : ""}
      </dl>
      <div class="link-row">${links.join(" ")}</div>
      <h3 class="profile-section-title">Usage &amp; reset</h3>
      ${usageHtml}
    `;
  }

  function openProfileAt(index) {
    if (!profileList.length) return;
    profileIndex = ((index % profileList.length) + profileList.length) % profileList.length;
    const overlay = $("#profile-overlay");
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    document.body.classList.add("profile-open");
    renderProfileSheet();
    $("#profile-close")?.focus();
  }

  function openProfileById(id) {
    loadProfiles();
    const idx = profileList.findIndex((p) => p.id === id);
    if (idx >= 0) openProfileAt(idx);
  }

  function closeProfile() {
    const overlay = $("#profile-overlay");
    if (!overlay) return;
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    document.body.classList.remove("profile-open");
    openProfileId = null;
  }

  function wireProfiles() {
    $$(".bot-row[data-profile-id]").forEach((row) => {
      const open = () => openProfileById(row.dataset.profileId);
      row.onclick = (e) => {
        if (e.target.closest("a, button, select, input, form, label")) return;
        open();
      };
      row.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      };
    });
    $$("[data-open-profile]").forEach((el) => {
      el.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        openProfileById(el.getAttribute("data-open-profile"));
      };
    });
  }

  function openFromQuery() {
    const id = new URLSearchParams(window.location.search).get("profile");
    if (id) openProfileById(id);
  }

  function boot() {
    initTheme();
    wireSortableTables();
    loadProfiles();
    wireProfiles();
    openFromQuery();
    $("#profile-prev")?.addEventListener("click", () => openProfileAt(profileIndex - 1));
    $("#profile-next")?.addEventListener("click", () => openProfileAt(profileIndex + 1));
    $("#profile-close")?.addEventListener("click", closeProfile);
    $$("[data-close-profile]").forEach((el) => el.addEventListener("click", closeProfile));
    document.addEventListener("keydown", (e) => {
      if ($("#profile-overlay")?.hidden) return;
      if (e.key === "Escape") closeProfile();
      if (e.key === "ArrowLeft") openProfileAt(profileIndex - 1);
      if (e.key === "ArrowRight") openProfileAt(profileIndex + 1);
    });
  }

  function shouldSkipPoll() {
    if (document.body.classList.contains("profile-open")) return true;
    const overlay = $("#profile-overlay");
    if (overlay && !overlay.hidden) return true;
    const active = document.activeElement;
    if (active && (active.tagName === "SELECT" || active.closest?.(".trade-select"))) return true;
    return false;
  }

  function capturePreserveState() {
    const selects = {};
    $$("[data-preserve-select]").forEach((el) => {
      const key = el.getAttribute("data-preserve-select") || el.id || el.name;
      if (key) selects[key] = el.value;
    });
    return { selects, openProfileId };
  }

  function restorePreserveState(state) {
    if (!state) return;
    Object.entries(state.selects || {}).forEach(([key, value]) => {
      $$(`[data-preserve-select="${key}"]`).forEach((el) => {
        if ([...el.options].some((o) => o.value === value)) el.value = value;
      });
    });
    if (state.openProfileId) {
      openProfileById(state.openProfileId);
    }
  }

  window.Leadlane = {
    shouldSkipPoll,
    capturePreserveState,
    restorePreserveState,
    afterPoll(state) {
      wireSortableTables();
      loadProfiles();
      wireProfiles();
      Object.keys(sortState).forEach((key) => {
        const table = $(`table[data-table="${key}"]`);
        if (table) sortTableDom(table);
        syncSortHeaders(key);
      });
      restorePreserveState(state);
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
