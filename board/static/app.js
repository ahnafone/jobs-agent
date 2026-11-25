/* ===========================================================================
   Applications Board — front end
   Plain JavaScript, no framework, no build step. Talks only to the JSON API
   in jobs_board.py. Everything the server sends is escaped before it reaches
   the page; links are only rendered when they are http(s).
   =========================================================================== */
(function () {
  "use strict";

  /* ---------------------------------------------------------------- utils */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const NET_MSG = "I can't reach the server just now. Check your connection and try again.";
  const WORKING = {
    queued: "Waiting its turn",
    reading: "Reading the job posting",
    writing_resume: "Writing your resume and cover letter",
    checking: "Checking the details against the posting"
  };
  const RESUME_FILES = [
    ["resume_pdf", "Resume (PDF)", "pdf"],
    ["letter_pdf", "Cover letter (PDF)", "pdf"],
    ["resume_docx", "Resume (Word)", "docx"],
    ["letter_docx", "Cover letter (Word)", "docx"]
  ];
  const SUGGESTIONS = ["What's new this week?", "Any replies I should answer?", "Which interviews are coming up?"];

  const STAGES = [
    { id: "new",       label: "New",                           short: "New",       hint: "Found but not applied yet", icon: "sparkle" },
    { id: "applied",   label: "Applied",                       short: "Applied",   hint: "Application sent",          icon: "send" },
    { id: "interview", label: "Interview",                     short: "Interview", hint: "Includes assessments",      icon: "chat" },
    { id: "offer",     label: "Offer",                         short: "Offer",     hint: "An offer is on the table",  icon: "star" },
    { id: "rejected",  label: "Rejected",                      short: "Rejected",  hint: "They said no",              icon: "circle-slash" },
    { id: "parked",    label: "Not eligible / not interested", short: "Not for me", hint: "Set aside",                 icon: "archive" }
  ];
  const STAGE_BY_ID = {};
  STAGES.forEach((s, i) => { STAGE_BY_ID[s.id] = s; s.order = i; });

  const RESPONSE_TYPES = {
    interview:  { label: "Interview",    icon: "chat" },
    assessment: { label: "Assessment",   icon: "clipboard" },
    rejection:  { label: "Rejection",    icon: "circle-slash" },
    offer:      { label: "Offer",        icon: "star" },
    info:       { label: "Info request", icon: "info" }
  };
  const FIT_OPTIONS = [
    { id: "all", label: "Any fit" }, { id: "80", label: "80 and up" }, { id: "65", label: "65 to 79" },
    { id: "40", label: "40 to 64" }, { id: "low", label: "Under 40" }
  ];
  const WHERE_OPTIONS = [
    { id: "all", label: "Anywhere" }, { id: "Remote", label: "Remote" }, { id: "Hybrid", label: "Hybrid" }, { id: "On-site", label: "On-site" }
  ];
  const ADDED_OPTIONS = [
    { id: "all", label: "Any time" }, { id: "7", label: "Last 7 days" }, { id: "30", label: "Last 30 days" }
  ];
  const SORTERS = {
    code: it => it.code,
    title: it => it.title.toLowerCase(),
    company: it => it.company.toLowerCase(),
    stage: it => stage(it.stage).order,
    fit: it => it.fit,
    location: it => locationOf(it).toLowerCase(),
    posted: it => (it.posted && it.posted.date ? day(it.posted.date) : -1e6),
    added: it => day(it.added)
  };

  const EMPTY_BOARD_HTML = $("#board-empty").innerHTML;

  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function enc(v) { return encodeURIComponent(String(v == null ? "" : v)); }
  function icon(name, size) {
    const s = size ? ' style="--od-icon-size:' + size + 'px"' : "";
    return '<svg class="od-icon" aria-hidden="true"' + s + '><use href="#i-' + name + '"></use></svg>';
  }
  function validDate(iso) { return /^\d{4}-\d{2}-\d{2}/.test(iso || ""); }
  function day(iso) {
    const p = String(iso).slice(0, 10).split("-").map(Number);
    return Date.UTC(p[0], p[1] - 1, p[2]) / 86400000;
  }
  function fmt(iso, withYear) {
    if (!validDate(iso)) return "";
    const p = String(iso).slice(0, 10).split("-").map(Number);
    return p[2] + " " + MONTHS[p[1] - 1] + (withYear ? " " + p[0] : "");
  }
  function todayISO() { return S.state && S.state.today ? S.state.today : new Date().toISOString().slice(0, 10); }
  function daysFromToday(iso) { return day(iso) - day(todayISO()); }
  function dueWords(iso) {
    const d = daysFromToday(iso);
    if (d < -1) return Math.abs(d) + " days ago";
    if (d === -1) return "yesterday";
    if (d === 0) return "today";
    if (d === 1) return "tomorrow";
    return "in " + d + " days";
  }
  function agoWords(sec) {
    if (sec < 60) return "just now";
    const m = Math.floor(sec / 60);
    if (m < 60) return m + (m === 1 ? " minute" : " minutes") + " ago";
    const h = Math.floor(m / 60);
    if (h < 48) return h + (h === 1 ? " hour" : " hours") + " ago";
    return Math.floor(h / 24) + " days ago";
  }
  function safeHref(u) {
    try {
      const p = new URL(u);
      return p.protocol === "http:" || p.protocol === "https:" ? p.href : "";
    } catch (e) { return ""; }
  }
  function stage(id) { return STAGE_BY_ID[id] || STAGES[0]; }
  function locationOf(it) {
    const where = it.workStyle || "";
    const place = it.city || (!where ? it.location || "" : "");
    if (where && place) return where + " · " + place;
    return where || place || "Location not stated";
  }
  function payOf(it) { return it.pay || "Pay not stated"; }
  function postedOf(it) {
    if (!it.posted || !it.posted.date) return "Posted date unknown";
    return "Posted " + (it.posted.approx ? "~" : "") + fmt(it.posted.date);
  }
  function latestReply(it) {
    if (!it.responses || !it.responses.length) return null;
    return it.responses.slice().sort((a, b) => day(b.date) - day(a.date))[0];
  }
  function fitTier(n) {
    if (n >= 80) return { key: "strong", word: "Strong fit" };
    if (n >= 65) return { key: "good", word: "Good fit" };
    if (n >= 40) return { key: "fair", word: "Fair fit" };
    return { key: "low", word: "Low fit" };
  }
  function isWorking(r) { return !!r && Object.prototype.hasOwnProperty.call(WORKING, r.status); }
  function friendlyError(e) {
    const s = String(e || "");
    if (!s || s.length > 220 || /traceback|exception|errno|\.py\b/i.test(s)) {
      return "Something went wrong while writing this one. Try again, or ask for changes.";
    }
    return s;
  }
  function knownCodes() {
    const set = new Set();
    if (S.state) {
      S.state.cards.forEach(c => set.add(c.code));
      S.state.inbox.forEach(c => set.add(c.code));
    }
    return set;
  }

  /* ----------------------------------------------------------- preferences */
  const PREFS_KEY = "applications-board.prefs";
  function readPrefs() {
    try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch (e) { return {}; }
  }
  function writePrefs() {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify({
        theme: S.theme, view: S.view, collapsed: S.collapsed,
        installDismissed: S.installDismissed, dupKept: S.dupKept.slice(-100)
      }));
    } catch (e) { /* private mode: fine */ }
  }
  // "Keep both" is remembered on this device only, as a sorted pair of codes.
  function pairKey(a, b) { return [a, b].sort().join("|"); }
  function isKeptPair(a, b) { return S.dupKept.indexOf(pairKey(a, b)) > -1; }

  /* ----------------------------------------------------------------- state */
  const S = {
    state: null,
    ended: false,
    view: "board",
    stage: "new",
    search: "",
    sort: { key: "added", dir: "desc" },
    filters: { stages: [], fit: "all", where: "all", added: "all", showLow: false },
    draft: null,
    theme: "light",
    collapsed: true,
    picked: [],
    activityOpen: false,
    activityHTML: "",
    activity: null,
    installDismissed: false,
    dupKept: [],
    dup: null,
    doc: null,
    chat: [],
    chatJob: null,
    chatLoaded: false,
    popup: null,
    popupKey: "",
    resumeCode: null,
    resume: null,
    msgs: { open: false, code: "", screen: "list", threads: [], loaded: false, query: "", unreadOnly: false,
            chat: null, busy: null, doc: "resume", refocus: false, marking: false, tick: 0 },
    pending: null,
    triggers: [],
    add: { mode: "input", job: null, text: "", anyway: false, picks: [] },
    timers: {},
    inflight: new Set()
  };

  function schedule(key, ms, fn) {
    clearTimeout(S.timers[key]);
    S.timers[key] = setTimeout(() => { delete S.timers[key]; if (!S.ended) fn(); }, ms);
  }
  function cancel(key) { clearTimeout(S.timers[key]); delete S.timers[key]; }

  /* ------------------------------------------------------------ network */
  class SessionEnded extends Error {}
  class NetError extends Error {}
  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  async function api(path, opts) {
    const o = opts || {};
    const method = o.method || "GET";
    const headers = { Accept: "application/json" };
    const init = { method: method, headers: headers, cache: "no-store", credentials: "same-origin" };
    if (method !== "GET") headers["X-Board"] = "1";
    if (o.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(o.body);
    }
    let res;
    try { res = await fetch(path, init); } catch (e) { throw new NetError(); }
    if (res.status === 401) throw new SessionEnded();
    // A proxy error page (a 502 while the board restarts) is a connection problem, not a signed-out session.
    const ctype = res.headers.get("Content-Type") || "";
    if (ctype.indexOf("application/json") === -1) throw new NetError();
    let data;
    try { data = await res.json(); } catch (e) { throw new NetError(); }
    if (!res.ok) throw new ApiError((data && data.error) || "Something went wrong. Try again.", res.status);
    return data;
  }

  // Wraps api(): returns the data, or null after showing the right message.
  async function request(path, opts) {
    if (S.ended) return null;
    try {
      return await api(path, opts);
    } catch (e) {
      if (e instanceof SessionEnded) { showSessionEnded(); return null; }
      if (e instanceof NetError) { toast(NET_MSG); setBanner("error", true); return null; }
      toast(e.message || "Something went wrong. Try again.");
      return null;
    }
  }

  // Runs a user action once at a time per key; a second click while busy is ignored.
  async function once(key, fn) {
    if (S.inflight.has(key)) return;
    S.inflight.add(key);
    try { return await fn(); } finally { S.inflight.delete(key); }
  }

  function showSessionEnded() {
    if (S.ended) return;
    S.ended = true;
    Object.keys(S.timers).forEach(cancel);
    closeCardSilently();
    S.msgs.open = false;
    $("#view-app").hidden = true;
    $("#view-login").hidden = false;
    $("#login-password").focus();
  }

  function loginError(msg) {
    const e = $("#login-error"), i = $("#login-password");
    e.textContent = msg;
    e.hidden = !msg;
    i.setAttribute("aria-invalid", msg ? "true" : "false");
  }

  async function signIn(ev) {
    ev.preventDefault();
    await once("login", async () => {
      const input = $("#login-password"), btn = $("#login-submit");
      if (!input.value) { loginError("Type your password."); input.focus(); return; }
      loginError("");
      btn.disabled = true;
      try {
        let res, data = {};
        try {
          res = await fetch("/api/login", {
            method: "POST", cache: "no-store", credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-Board": "1", Accept: "application/json" },
            body: JSON.stringify({ password: input.value })
          });
        } catch (e) { loginError(NET_MSG); return; }
        try { data = await res.json(); } catch (e) { /* not JSON: fall through to the generic message */ }
        if (res.ok && data.ok) { location.reload(); return; }
        input.value = "";
        loginError(data.error || "I couldn't sign you in. Try again.");
        input.focus();
      } finally { btn.disabled = false; }
    });
  }

  async function signOut() {
    await once("signout", async () => {
      try {
        await fetch("/api/logout", { method: "POST", cache: "no-store", credentials: "same-origin", headers: { "X-Board": "1" } });
      } catch (e) { toast(NET_MSG); return; }
      location.reload();
    });
  }

  function setBanner(which, on) {
    if (which === "error") $("#banner-error").hidden = !on;
    if (which === "offline") $("#banner-offline").hidden = !on;
  }

  async function loadState() {
    if (S.ended) return;
    const data = await request("/api/state");
    if (!data) return;
    S.state = data;
    setBanner("error", false);
    if (S.activityOpen) loadActivity();
    render();
    if (!S.routed) { S.routed = true; route(); }
  }

  async function loadActivity() {
    const data = await request("/api/activity?limit=60");
    if (!data) return;
    S.activity = data.items || [];
    renderActivity();
  }

  /* -------------------------------------------------------------- toasts */
  function toast(message, undoFn) {
    const host = $("#toasts");
    const el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("role", "status");
    el.innerHTML = '<span class="toast__msg">' + esc(message) + "</span>" +
      (undoFn ? '<button class="toast__undo" type="button" data-act="undo">' + icon("undo", 15) + " Undo</button>" : "") +
      '<button class="toast__close" type="button" data-act="toast-close">' + icon("x", 16) + '<span class="sr-only">Dismiss this message</span></button>';
    host.appendChild(el);
    const close = () => {
      if (!el.isConnected) return;
      el.classList.add("is-leaving");
      setTimeout(() => el.remove(), 180);
    };
    const timer = setTimeout(close, undoFn ? 9000 : 5000);
    el.addEventListener("click", ev => {
      const btn = ev.target.closest("button");
      if (!btn) return;
      const act = btn.getAttribute("data-act");
      if (act === "undo" && undoFn) { clearTimeout(timer); close(); undoFn(); }
      if (act === "toast-close") { clearTimeout(timer); close(); }
    });
    return el;
  }

  /* ----------------------------------------------------------- filtering */
  function matchesSearch(it) {
    const q = S.search.trim().toLowerCase();
    if (!q) return true;
    return (it.title + " " + it.company + " " + it.code + " " + locationOf(it)).toLowerCase().indexOf(q) > -1;
  }
  function matchesFilters(it) {
    const f = S.filters;
    if (f.stages.length && f.stages.indexOf(it.stage) === -1) return false;
    if (f.fit === "80" && it.fit < 80) return false;
    if (f.fit === "65" && (it.fit < 65 || it.fit > 79)) return false;
    if (f.fit === "40" && (it.fit < 40 || it.fit > 64)) return false;
    if (f.fit === "low" && it.fit >= 40) return false;
    if (f.where !== "all" && it.workStyle !== f.where) return false;
    if (f.added !== "all" && Math.abs(daysFromToday(it.added)) > Number(f.added)) return false;
    return true;
  }
  function visibleCards() { return S.state.cards.filter(it => matchesSearch(it) && matchesFilters(it)); }
  function activeFilterCount() {
    const f = S.filters;
    let n = 0;
    if (f.stages.length) n++;
    if (f.fit !== "all") n++;
    if (f.where !== "all") n++;
    if (f.added !== "all") n++;
    return n;
  }
  function hasActiveFilters() { return activeFilterCount() > 0 || !!S.search.trim(); }
  // Saved views also remember "show low fit scores", which isn't counted as a filter.
  function hasViewFilters() { return hasActiveFilters() || !!S.filters.showLow; }
  function showFit(it) { return it.fit >= 40 || S.filters.showLow; }

  /* ------------------------------------------------------------ fragments */
  function fitBadge(it) {
    if (!showFit(it)) return "";
    const t = fitTier(it.fit);
    return '<span class="fit fit--' + t.key + ' od-fixed"><span class="sr-only">Fit score </span>' +
      '<span class="fit__num">' + esc(it.fit) + '</span><span class="fit__word">' + t.word + "</span></span>";
  }
  function stageChip(it, asButton) {
    const st = stage(it.stage);
    if (!asButton) {
      return '<span class="stagechip stagechip--static stagechip--' + st.id + '">' + icon(st.icon, 15) +
        "<span>" + esc(st.short) + "</span></span>";
    }
    return '<button class="stagechip stagechip--' + st.id + '" type="button" data-act="stage" data-code="' + esc(it.code) +
      '" aria-haspopup="dialog" aria-label="Stage: ' + esc(st.label) + '. Choose a different stage">' +
      icon(st.icon, 15) + "<span>" + esc(st.short) + "</span>" + icon("chevron-down", 14) + "</button>";
  }
  function sourceTag(it) {
    const glyph = it.source === "Email" ? "mail" : it.source === "Chat link" ? "link" : it.source === "Pasted link" ? "link" : "chat";
    return '<span class="tag">' + icon(glyph, 14) + "<span>" + esc(it.source || "Found") + "</span></span>";
  }
  function replyBlock(it) {
    const r = latestReply(it);
    if (!r) return "";
    const meta = RESPONSE_TYPES[r.type] || { label: r.type, icon: "info" };
    return '<div class="reply reply--' + esc(r.type) + '">' +
      '<p class="reply__top">' + icon(meta.icon, 16) + "<span>" + esc(meta.label) + " · " + fmt(r.date) + "</span></p>" +
      '<p class="reply__note">' + esc(r.note) + "</p>" +
      (r.deadline ? '<p class="reply__due">Reply by ' + fmt(r.deadline) + " — " + dueWords(r.deadline) + "</p>" : "") +
      "</div>";
  }
  function eligLine(text, pass, extra) {
    return '<p class="elig ' + (pass ? "elig--pass" : "elig--fail") + (extra ? " " + extra : "") + '">' +
      icon(pass ? "check" : "circle-slash", 16) + "<span>" + esc(text) + "</span></p>";
  }
  function aiTag(it) {
    if (!it.updatedByAssistant) return "";
    return '<span class="tag tag--ai">' + icon("sparkle", 14) + "<span>Updated from your email</span></span>";
  }
  function resumeTag(it) {
    const r = it.resume;
    if (!r) return "";
    if (isWorking(r)) return '<span class="tag">' + icon("refresh", 14) + "<span>Writing resume…</span></span>";
    if (r.status === "needs_input") return '<span class="tag">' + icon("note", 14) + "<span>Needs your answers</span></span>";
    if (r.status === "ready") return '<span class="tag tag--ready">' + icon("file", 14) + "<span>Resume ready to review</span></span>";
    if (r.status === "approved") return '<span class="tag tag--ready">' + icon("file", 14) + "<span>Resume approved</span></span>";
    return "";
  }
  // The first possible duplicate that is still on the board and that you haven't said to keep separate.
  function dupOf(it) {
    const other = (it.possibleDuplicateOf || []).find(c => findCard(c) && !isKeptPair(it.code, c));
    return other ? findCard(other) : null;
  }
  function dupTag(it) {
    if (!dupOf(it)) return "";
    return '<span class="tag tag--dup">' + icon("merge", 14) + "<span>Possible duplicate</span></span>";
  }
  function openLink(url, title, company, cls, fid) {
    const href = safeHref(url);
    if (!href) return "";
    return '<a class="' + (cls || "card__link") + '" href="' + esc(href) + '" target="_blank" rel="noopener noreferrer" data-fid="' + esc(fid) + '">' +
      icon("external", 16) + "<span>Open job posting</span><span class=\"sr-only\"> for " + esc(title) + " at " + esc(company) +
      " (opens in a new tab)</span></a>";
  }

  /* ---------------------------------------------------------------- cards */
  function cardHTML(it) {
    const open = openLink(it.url, it.title, it.company, "card__link", it.code + ":link");
    return '<article class="card" data-code="' + esc(it.code) + '" data-fid="' + esc(it.code) + ':card" ' +
      'role="button" tabindex="0" aria-label="' + esc(it.title) + " at " + esc(it.company) + ', open the full details">' +
      '<div class="od-row-top card__top">' +
        '<div class="od-field od-fill card__head"><p class="card__company">' + esc(it.company) + "</p>" +
        '<h3 class="card__title od-clamp-2">' + esc(it.title) + "</h3></div>" +
        fitBadge(it) +
        '<span class="card__grip" aria-hidden="true">' + icon("grip", 16) + "</span>" +
      "</div>" +
      (showFit(it) && it.fitReason ? '<p class="card__reason od-clamp-2">' + esc(it.fitReason) + "</p>" : "") +
      (it.stage === "parked" && it.reason ? eligLine(it.reason, false, "card__elig") : "") +
      '<ul class="card__meta"><li>' + icon("pin", 15) + "<span>" + esc(locationOf(it)) + "</span></li>" +
      "<li>" + icon("wallet", 15) + "<span>" + esc(payOf(it)) + "</span></li></ul>" +
      replyBlock(it) +
      '<p class="od-cluster card__dates"><span class="od-nowrap">' + esc(postedOf(it)) + "</span>" +
      '<span aria-hidden="true">·</span><span class="od-nowrap">Added ' + fmt(it.added) + "</span></p>" +
      '<div class="od-cluster card__foot">' + stageChip(it, true) + sourceTag(it) + aiTag(it) + resumeTag(it) + dupTag(it) +
        '<span class="code od-nowrap"><span class="sr-only">Code </span>' + esc(it.code) + "</span></div>" +
      '<div class="card__acts">' + open +
        '<button class="btn btn--icon od-fixed" type="button" data-act="actions" data-code="' + esc(it.code) + '" aria-haspopup="dialog">' +
        icon("dots", 20) + '<span class="sr-only">Quick actions for ' + esc(it.title) + "</span></button></div>" +
      "</article>";
  }

  /* ----------------------------------------------------------------- board */
  function renderStageBar(counts) {
    $("#stagebar").innerHTML = STAGES.map(st => {
      const on = st.id === S.stage;
      return '<button class="stage-tab" type="button" role="tab" data-stage="' + st.id + '" aria-selected="' + on +
        '" tabindex="' + (on ? "0" : "-1") + '">' + icon(st.icon, 16) + "<span>" + esc(st.short) + "</span>" +
        '<span class="stage-tab__count"><span class="sr-only">, </span>' + counts[st.id] +
        '<span class="sr-only"> cards</span></span></button>';
    }).join("");
  }
  function emptyColumnLine(st) {
    switch (st.id) {
      case "new": return "No new finds right now. The assistant will add them here.";
      case "applied": return "Nothing sent yet. Move a card here once you apply.";
      case "interview": return "No interviews or assessments booked yet.";
      case "offer": return "No offers yet.";
      case "rejected": return "No rejections here.";
      default: return "Nothing set aside.";
    }
  }
  function renderBoard() {
    const rows = visibleCards();
    const counts = {};
    STAGES.forEach(st => { counts[st.id] = rows.filter(it => it.stage === st.id).length; });
    renderStageBar(counts);
    $("#board").innerHTML = STAGES.map(st => {
      const mine = rows.filter(it => it.stage === st.id);
      const isParked = st.id === "parked";
      const collapsed = isParked && S.collapsed;
      return '<section class="column' + (collapsed ? " column--collapsed" : "") + (st.id === S.stage ? " is-active" : "") +
        '" data-stage="' + st.id + '" aria-labelledby="col-' + st.id + '">' +
        '<div class="column__head"><h3 class="column__title" id="col-' + st.id + '">' + icon(st.icon, 16) +
        "<span>" + esc(st.label) + "</span></h3>" +
        '<span class="column__count">' + mine.length + '<span class="sr-only"> cards</span></span>' +
        (isParked
          ? '<button class="btn btn--icon od-fixed column__toggle" type="button" data-act="collapse" aria-expanded="' + (!collapsed) + '">' +
            icon(collapsed ? "chevron-right" : "chevron-down", 18) + '<span class="sr-only">' + (collapsed ? "Show" : "Hide") +
            " the " + esc(st.label) + " column</span></button>"
          : "") +
        "</div>" +
        '<div class="column__list">' + (collapsed ? "" : mine.map(cardHTML).join("")) + "</div>" +
        (mine.length ? "" : '<p class="column__empty">' + esc(emptyColumnLine(st)) + "</p>") +
        "</section>";
    }).join("");
    const empty = rows.length === 0 && S.state.cards.length === 0;
    $("#board-empty").hidden = !empty;
    $("#board").hidden = empty;
    $("#stagebar").hidden = empty;
    if (empty) $("#board-empty").innerHTML = EMPTY_BOARD_HTML;
    if (!empty && rows.length === 0) {
      $("#board").innerHTML = '<p class="empty__title">No applications match</p><p>Try a shorter search, or clear a filter or two.</p>' +
        '<button class="btn btn--outline" type="button" data-act="clear-all">Clear search and filters</button>';
    }
  }

  /* ------------------------------------------------------------------ list */
  function sortedCards() {
    const get = SORTERS[S.sort.key] || SORTERS.added;
    const dir = S.sort.dir === "asc" ? 1 : -1;
    return visibleCards().slice().sort((a, b) => {
      const x = get(a), y = get(b);
      if (x < y) return -1 * dir;
      if (x > y) return 1 * dir;
      return a.code < b.code ? -1 : 1;
    });
  }
  function renderList() {
    const rows = sortedCards();
    $$("#table thead th[data-sort]").forEach(th => {
      const key = th.getAttribute("data-sort");
      const use = $("use", th);
      if (key === S.sort.key) {
        th.setAttribute("aria-sort", S.sort.dir === "asc" ? "ascending" : "descending");
        if (use) use.setAttribute("href", S.sort.dir === "asc" ? "#i-sort" : "#i-sort-down");
      } else {
        th.removeAttribute("aria-sort");
        if (use) use.setAttribute("href", "#i-sort");
      }
    });
    const postedTxt = it => (it.posted && it.posted.date ? (it.posted.approx ? "~" : "") + fmt(it.posted.date) : "Unknown");
    $("#table-body").innerHTML = rows.map(it =>
      '<tr data-code="' + esc(it.code) + '" data-fid="' + esc(it.code) + ':row">' +
        '<td><span class="code">' + esc(it.code) + "</span></td>" +
        '<td class="cell-title"><button class="t-btn" type="button" data-act="open-card" data-code="' + esc(it.code) + '">' + esc(it.title) + "</button></td>" +
        "<td>" + esc(it.company) + "</td>" +
        "<td>" + stageChip(it, false) + "</td>" +
        '<td class="cell-num">' + (showFit(it) ? fitBadge(it) : '<span class="sr-only">Not shown</span>—') + "</td>" +
        "<td>" + esc(locationOf(it)) + "</td>" +
        '<td class="od-nowrap">' + esc(postedTxt(it)) + "</td>" +
        '<td class="od-nowrap">' + fmt(it.added) + "</td>" +
        '<td class="cell-link">' + openLink(it.url, it.title, it.company, "btn btn--quiet btn--sm", it.code + ":link") + "</td>" +
      "</tr>"
    ).join("");
    $("#rows").innerHTML = rows.map(it =>
      '<article class="rowcard" data-code="' + esc(it.code) + '" data-fid="' + esc(it.code) + ':rowcard" role="button" tabindex="0" ' +
        'aria-label="' + esc(it.title) + " at " + esc(it.company) + ', open the full details">' +
        '<div class="od-row-top rowcard__top"><div class="od-field od-fill">' +
          '<p class="card__company">' + esc(it.company) + '</p><h3 class="card__title">' + esc(it.title) + "</h3></div>" + fitBadge(it) + "</div>" +
        '<div class="od-cluster">' + stageChip(it, true) + aiTag(it) + resumeTag(it) + dupTag(it) +
          '<span class="code od-nowrap"><span class="sr-only">Code </span>' + esc(it.code) + "</span></div>" +
        '<dl class="rowcard__defs"><dt>Location</dt><dd>' + esc(locationOf(it)) + "</dd><dt>Pay</dt><dd>" + esc(payOf(it)) +
        "</dd><dt>Posted</dt><dd>" + esc(postedTxt(it)) + "</dd><dt>Added</dt><dd>" + fmt(it.added) + "</dd></dl>" +
        (it.stage === "parked" && it.reason ? eligLine(it.reason, false) : "") +
        '<div class="card__acts">' + openLink(it.url, it.title, it.company, "card__link", it.code + ":rowlink") +
          '<button class="btn btn--icon od-fixed" type="button" data-act="actions" data-code="' + esc(it.code) + '" aria-haspopup="dialog">' +
          icon("dots", 20) + '<span class="sr-only">Quick actions for ' + esc(it.title) + "</span></button></div>" +
      "</article>"
    ).join("");
    const none = rows.length === 0;
    $("#list-empty").hidden = !none;
    $(".table-wrap").hidden = none;
    $("#rows").hidden = none;
    $(".listsort").hidden = none;
    $("#sort-select").value = S.sort.key + "-" + S.sort.dir;
  }

  /* ----------------------------------------------------------------- inbox */
  function inboxItemHTML(it) {
    const picked = S.picked.indexOf(it.code) > -1;
    return '<li class="card inbox-item" data-code="' + esc(it.code) + '" data-fid="' + esc(it.code) + ':inbox">' +
      '<label class="inbox-item__pick"><input type="checkbox" data-act="pick-inbox" data-code="' + esc(it.code) + '"' +
        (picked ? " checked" : "") + ' aria-label="Select ' + esc(it.title) + " at " + esc(it.company) + '"><span>Select</span></label>' +
      '<div class="od-row-top"><div class="od-field od-fill card__head"><p class="card__company">' + esc(it.company) + "</p>" +
        '<h3 class="card__title od-clamp-2">' + esc(it.title) + "</h3></div>" + fitBadge(it) + "</div>" +
      (showFit(it) && it.fitReason ? '<p class="card__reason od-clamp-2">' + esc(it.fitReason) + "</p>" : "") +
      '<p class="inbox-item__found">' + icon("info", 15) + "<span>" + esc(it.found || "Found by your assistant") + "</span></p>" +
      '<ul class="card__meta"><li>' + icon("pin", 15) + "<span>" + esc(locationOf(it)) + "</span></li>" +
      "<li>" + icon("wallet", 15) + "<span>" + esc(payOf(it)) + "</span></li></ul>" +
      '<div class="od-cluster card__foot">' + sourceTag(it) + "</div>" +
      '<div class="inbox-item__acts">' +
        '<button class="btn btn--primary btn--sm" type="button" data-act="accept" data-code="' + esc(it.code) + '">' + icon("check", 16) + "<span>Add to my board</span></button>" +
        '<button class="btn btn--outline btn--sm" type="button" data-act="decline" data-code="' + esc(it.code) + '">Not for me</button>' +
        '<button class="btn btn--quiet btn--sm" type="button" data-act="open-card" data-code="' + esc(it.code) + '">Details</button>' +
      "</div></li>";
  }
  function ineligibleHTML(e) {
    return '<li class="card inbox-item" data-fid="' + esc(e.id) + ':ineligible">' +
      '<div class="od-row-top"><div class="od-field od-fill card__head"><p class="card__company">' + esc(e.company) + "</p>" +
        '<h3 class="card__title od-clamp-2">' + esc(e.title) + "</h3></div></div>" +
      eligLine(e.reason || "Didn't pass your checks", false) +
      '<ul class="card__meta"><li>' + icon("pin", 15) + "<span>" + esc(locationOf(e)) + "</span></li></ul>" +
      '<p class="inbox-item__found">' + icon("clock", 15) + "<span>Found " + esc(fmt(e.added)) + " from " + esc(e.source) + "</span></p>" +
      '<div class="inbox-item__acts">' + openLink(e.url, e.title, e.company, "btn btn--outline btn--sm", e.id + ":link") +
        '<button class="btn btn--primary btn--sm" type="button" data-act="ineligible-add" data-id="' + esc(e.id) + '">Add anyway</button></div>' +
      "</li>";
  }
  function renderInbox() {
    const st = S.state;
    const pending = st.inbox || [];
    const inelig = st.ineligible || [];
    S.picked = S.picked.filter(c => pending.some(x => x.code === c));
    $("#inbox-list").innerHTML = pending.map(inboxItemHTML).join("");
    $("#inbox-empty").hidden = pending.length > 0 || inelig.length > 0;
    const n = S.picked.length;
    $("#inbox-bulk").hidden = n === 0;
    $("#inbox-selected").textContent = n + " selected";
    $("#bulk-accept-label").textContent = "Add " + n;
    $("#bulk-decline-label").textContent = "Decline " + n;
    $("#inbox-select-all").textContent = pending.length && n === pending.length ? "Clear selection" : "Select all";
    $("#ineligible-block").hidden = inelig.length === 0;
    $("#ineligible-list").innerHTML = inelig.map(ineligibleHTML).join("");
  }

  /* ------------------------------------------------------- attention strip */
  function renderAttention() {
    const list = S.state.attention || [];
    $("#attention").hidden = list.length === 0;
    $("#attention-items").innerHTML = list.map(a => {
      const label = a.kind === "deadline" ? "Reply due" : a.kind === "reply" ? "Reply to read" : "Follow-up";
      const tone = a.kind === "deadline" ? " alert-card--info" : "";
      const when = a.kind === "deadline"
        ? '<p class="alert-card__when">' + icon("clock", 15) + " Reply by " + fmt(a.due) + " — " + dueWords(a.due) + "</p>"
        : "";
      return '<article class="alert-card' + tone + '" data-code="' + esc(a.code) + '">' +
        '<div class="alert-card__top">' + icon(a.kind === "followup" ? "clock" : "chat", 18) +
        '<span class="alert-card__kind od-fill">' + esc(label) + "</span>" +
        '<button class="btn btn--icon od-fixed" type="button" data-act="att-dismiss" data-id="' + esc(a.id) + '">' + icon("x", 18) +
        '<span class="sr-only">Dismiss this reminder</span></button></div>' +
        '<p class="alert-card__who">' + esc(a.title) + "</p>" +
        '<p class="alert-card__ask">' + esc(a.text) + "</p>" + when +
        '<div class="alert-card__acts">' +
          '<button class="btn btn--outline btn--sm" type="button" data-act="open-card" data-code="' + esc(a.code) + '">Open details</button>' +
          '<button class="btn btn--quiet btn--sm" type="button" data-act="att-snooze" data-id="' + esc(a.id) + '">Remind me in 3 days</button>' +
        "</div></article>";
    }).join("");
  }

  /* ------------------------------------------------------ mail check row */
  function renderMail() {
    const st = S.state;
    const requested = st.checkRequestedAt || 0;
    const last = st.lastChecked || 0;
    const checking = requested > last && st.now - requested < 600;
    const btn = $('[data-act="check-email"]');
    $("#check-label").textContent = checking ? "Checking…" : "Check my email now";
    btn.disabled = checking;
    btn.setAttribute("aria-busy", String(checking));
    $("#mail-when").textContent = checking
      ? "Checking your email. New replies appear here in a few minutes."
      : last ? "Last checked " + agoWords(st.now - last) : "Not checked yet";
    if (checking) schedule("checkPoll", 10000, loadState);
    else cancel("checkPoll");
  }

  /* ---------------------------------------------------------- saved views */
  const MAX_VIEWS = 20;   // the server keeps the last 20
  function cleanFilters(f) {
    f = f || {};
    return {
      stages: Array.isArray(f.stages) ? f.stages.filter(id => STAGES.some(st => st.id === id)) : [],
      fit: FIT_OPTIONS.some(o => o.id === f.fit) ? f.fit : "all",
      where: WHERE_OPTIONS.some(o => o.id === f.where) ? f.where : "all",
      added: ADDED_OPTIONS.some(o => o.id === f.added) ? f.added : "all",
      showLow: !!f.showLow
    };
  }
  function viewKey(f, search) {
    const c = cleanFilters(f);
    return JSON.stringify([c.stages.slice().sort(), c.fit, c.where, c.added, c.showLow, String(search || "").trim().toLowerCase()]);
  }
  // The saved view that matches what is on screen right now, if any.
  function currentView() {
    if (!S.state || !hasViewFilters()) return null;
    const now = viewKey(S.filters, S.search);
    return (S.state.views || []).find(v => viewKey(v.filters, (v.filters || {}).search) === now) || null;
  }
  function optLabel(list, id) { const o = list.find(x => x.id === id); return o ? o.label : id; }
  function filterWords(f, search) {
    const c = cleanFilters(f);
    const parts = [];
    const q = String(search || "").trim();
    if (q) parts.push("Search “" + q + "”");
    if (c.stages.length) parts.push(c.stages.map(id => stage(id).label).join(", "));
    if (c.fit !== "all") parts.push("Fit " + optLabel(FIT_OPTIONS, c.fit).toLowerCase());
    if (c.where !== "all") parts.push(optLabel(WHERE_OPTIONS, c.where));
    if (c.added !== "all") parts.push("Added in the " + optLabel(ADDED_OPTIONS, c.added).toLowerCase());
    if (c.showLow) parts.push("Low fits shown");
    return parts.length ? parts.join(" · ") : "Everything on your board";
  }
  function renderViewChip() {
    const chip = $("#view-current");
    const v = currentView();
    chip.hidden = !v;
    chip.textContent = v ? v.name : "";
    chip.title = v ? v.name : "";
  }
  function renderViews() {
    const box = $("#dlg-views-body");
    const views = (S.state && S.state.views) || [];
    const cur = currentView();
    if (!views.length) {
      box.innerHTML = '<p class="viewempty">No saved views yet. Set up a search and some filters, then save them here so you can come back to the same list.</p>';
      return;
    }
    box.innerHTML = '<div class="viewlist">' + views.map(v => {
      const on = !!cur && cur.name === v.name;
      return '<div class="viewrow' + (on ? " is-on" : "") + '">' +
        '<button class="viewrow__pick" type="button" data-act="view-use" data-name="' + esc(v.name) + '"' + (on ? ' aria-current="true"' : "") + ">" +
          '<span class="viewrow__name">' + esc(v.name) + "</span>" +
          '<span class="viewrow__sum">' + esc(filterWords(v.filters, (v.filters || {}).search)) + "</span>" +
        "</button>" +
        '<button class="btn btn--icon od-fixed" type="button" data-act="view-delete" data-name="' + esc(v.name) + '">' +
          icon("trash", 18) + '<span class="sr-only">Delete ' + esc(v.name) + "</span></button>" +
      "</div>";
    }).join("") + "</div>";
  }
  function openViews(trigger) {
    renderViews();
    openDialog("dlg-views", trigger);
  }
  function applyView(name) {
    const v = ((S.state && S.state.views) || []).find(x => x.name === name);
    if (!v) return;
    S.filters = cleanFilters(v.filters);
    S.search = typeof (v.filters || {}).search === "string" ? v.filters.search : "";
    $("#search").value = S.search;
    closeDialog("dlg-views");
    render();
    toast("Showing “" + v.name + "”.");
  }
  function openViewName(trigger) {
    if (!hasViewFilters()) { toast("Set up a search or a filter first, then save it."); return; }
    $("#dlg-view-name-sub").textContent = filterWords(S.filters, S.search);
    $("#view-name-input").value = "";
    $("#view-name-error").hidden = true;
    openDialog("dlg-view-name", trigger, $("#view-name-input"));
  }
  async function saveView() {
    const input = $("#view-name-input");
    const name = input.value.replace(/\s+/g, " ").trim().slice(0, 40);
    const err = $("#view-name-error");
    const showErr = msg => { err.textContent = msg; err.hidden = false; input.focus(); };
    if (!name) { showErr("Give the view a name first."); return; }
    const views = (S.state && S.state.views) || [];
    const replacing = views.some(v => v.name === name);
    if (!replacing && views.length >= MAX_VIEWS) { showErr("You have " + MAX_VIEWS + " saved views. Delete one before saving another."); return; }
    const filters = Object.assign(cleanFilters(S.filters), { search: S.search });
    const d = await request("/api/views", { method: "POST", body: { name: name, filters: filters } });
    if (!d) return;
    S.state.views = d.views || S.state.views;
    closeDialog("dlg-view-name");
    renderViews();
    render();
    toast((replacing ? "Replaced the view “" : "Saved the view “") + name + "”.");
  }
  async function deleteView(name) {
    const d = await request("/api/views/" + enc(name), { method: "DELETE" });
    if (!d) return;
    S.state.views = d.views || [];
    renderViews();
    render();
    const first = $("#dlg-views .viewrow__pick") || $('#dlg-views [data-act="view-save"]');
    if (first) first.focus();
    toast("View deleted.");
  }

  /* --------------------------------------------------------- your progress */
  function chartSVG(bars) {
    const W = 560, H = 190, padL = 34, padB = 34, padT = 18, padR = 8;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const top = Math.max(4, Math.max.apply(null, bars.map(b => b.n)) + 1);
    const step = Math.ceil(top / 4);
    const slot = plotW / bars.length;
    const bw = Math.min(54, slot * 0.56);
    let grid = "", marks = "";
    for (let g = 0; g <= top; g += step) {
      const y = padT + plotH - (g / top) * plotH;
      grid += '<line class="chart__grid" x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) + '"></line>' +
        '<text class="chart__tick" x="' + (padL - 8) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="end">' + g + "</text>";
    }
    bars.forEach((b, i) => {
      const h = (b.n / top) * plotH;
      const x = padL + slot * i + (slot - bw) / 2;
      const y = padT + plotH - h;
      const last = i === bars.length - 1;
      marks += '<rect class="chart__bar' + (last ? "" : " chart__bar--muted") + '" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) +
        '" width="' + bw.toFixed(1) + '" height="' + Math.max(h, b.n ? 3 : 0).toFixed(1) + '" rx="3"></rect>';
      if (b.n) marks += '<text class="chart__value" x="' + (x + bw / 2).toFixed(1) + '" y="' + (y - 6).toFixed(1) + '" text-anchor="middle">' + b.n + "</text>";
      marks += '<text class="chart__tick" x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - 12) + '" text-anchor="middle">' + esc(b.label) + "</text>";
    });
    return '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" role="img" ' +
      'aria-label="' + esc("Applications sent each week: " + bars.map(b => "week of " + b.label + ", " + b.n).join("; ")) + '">' +
      grid + marks +
      '<line class="chart__base" x1="' + padL + '" y1="' + (padT + plotH) + '" x2="' + (W - padR) + '" y2="' + (padT + plotH) + '"></line>' +
      "</svg>";
  }
  function deadlineCount() {
    if (!S.state) return 0;
    const today = S.state.today || new Date().toISOString().slice(0, 10);
    return S.state.cards.filter(it => (it.responses || []).some(x => validDate(x.deadline) && x.deadline.slice(0, 10) >= today)).length;
  }
  function renderStats() {
    const s = (S.state && S.state.stats) || {};
    const weeks = Array.isArray(s.weeks) ? s.weeks : [];
    const bars = weeks.map(w => ({ n: Number(w.applied) || 0, label: fmt(w.weekOf) }));
    const perWeek = bars.length ? bars.reduce((a, b) => a + b.n, 0) / bars.length : 0;
    const sent = Number(s.applied) || 0;
    const replied = Number(s.replied) || 0;
    const median = s.medianDaysToReply == null ? null : Math.round(Number(s.medianDaysToReply) * 10) / 10;
    const deadlines = deadlineCount();
    $("#ics-all").hidden = !deadlines;   // an empty calendar file isn't worth downloading
    $("#dlg-stats-body").innerHTML =
      '<div class="statgrid">' +
        '<div class="statcard"><p class="statcard__key">Applications a week</p>' +
          '<p class="statcard__val">' + esc(Math.round(perWeek * 10) / 10) + "</p>" +
          '<p class="statcard__note">' + (bars.length ? "Averaged over the last " + bars.length + (bars.length === 1 ? " week." : " weeks.") : "Nothing sent yet.") + "</p></div>" +
        '<div class="statcard"><p class="statcard__key">Reply rate</p>' +
          '<p class="statcard__val">' + esc(Number(s.replyRate) || 0) + "%</p>" +
          '<p class="statcard__note">' + esc(replied + " of " + sent + (sent === 1 ? " application" : " applications") + " heard back.") + "</p></div>" +
        '<div class="statcard"><p class="statcard__key">Days to first reply</p>' +
          '<p class="statcard__val">' + (median === null ? "—" : esc(median)) + "</p>" +
          '<p class="statcard__note">' + (median === null ? "No replies yet." : "Median across the ones that replied.") + "</p></div>" +
      "</div>" +
      '<div class="chartblock">' +
        '<p class="chartblock__title">Applications sent each week</p>' +
        '<p class="chartblock__sub">Counted from the day each one was marked applied.</p>' +
        (bars.length ? chartSVG(bars) : '<p class="addnote">No applications yet.</p>') +
      "</div>" +
      '<p class="dupnote">' + (deadlines
        ? deadlines + " job" + (deadlines === 1 ? " has" : "s have") + " a date to keep. Download them and they land in your calendar."
        : "No dates to keep right now.") + "</p>";
  }
  function openStats(trigger) {
    renderStats();
    openDialog("dlg-stats", trigger, $('#dlg-stats .sheet__foot [data-close]'));
  }

  /* --------------------------------------------------------- activity log */
  function actAgo(ts) { return ts && S.state ? agoWords(S.state.now - ts) : ""; }
  function renderActivity() {
    const box = $("#activity-log");
    // The log refreshes in the background; put keyboard focus back on the same button afterwards.
    const active = document.activeElement;
    const hadFocus = !!active && box.contains(active);
    const fid = hadFocus ? active.getAttribute("data-fid") : null;
    const html = activityHTML();
    if (html === S.activityHTML && box.innerHTML) return;   // nothing new; skip the repaint
    S.activityHTML = html;
    box.innerHTML = html;
    if (!hadFocus) return;
    const again = fid && box.querySelector('[data-fid="' + CSS.escape(fid) + '"]');
    (again || $('[data-act="close-activity"]')).focus();
  }
  function actFid(a, what) { return "act:" + (a.ts || 0) + ":" + (a.code || "") + ":" + String(a.text || "").slice(0, 40) + ":" + what; }
  function activityHTML() {
    const acts = S.activity;
    if (acts === null) return '<p class="addnote">Loading…</p>';
    if (!acts.length) return '<p class="addnote">Nothing yet. Changes you make and changes the assistant makes will show up here.</p>';
    return acts.map((a, i) => {
      const you = a.by === "you";
      const job = [a.title, a.company].filter(Boolean).join(" · ");
      return '<article class="act ' + (you ? "act--you" : "act--bot") + '">' +
        '<span class="act__mark" aria-hidden="true">' + icon(you ? "check" : "sparkle", 16) + "</span>" +
        '<div class="act__body">' +
          '<p class="act__who"><span>' + (you ? "You" : "Assistant") + "</span>" +
            (a.ts ? '<span class="act__when">' + esc(actAgo(a.ts)) + "</span>" : "") + "</p>" +
          '<p class="act__text">' + esc(a.text) + "</p>" +
          (job ? '<p class="act__job">' + esc(job) + "</p>" : "") +
          '<div class="act__acts">' +
            (a.code && findCard(a.code) ? '<button class="btn btn--quiet btn--sm" type="button" data-act="open-card" data-code="' + esc(a.code) +
              '" data-fid="' + esc(actFid(a, "open")) + '">Open the card</button>' : "") +
            (a.undo ? '<button class="btn btn--quiet btn--sm" type="button" data-act="undo-activity" data-idx="' + i + '" data-fid="' + esc(actFid(a, "undo")) + '">' +
              icon("undo", 16) + "<span>Undo</span></button>" : "") +
          "</div>" +
        "</div>" +
      "</article>";
    }).join("");
  }
  function setActivityOpen(open, trigger) {
    if (open && !$("#chat").hidden) setChatOpen(false);
    S.activityOpen = open;
    $("#activity").hidden = !open;
    $("#view-app").classList.toggle("has-activity", open);
    $('[data-act="open-activity"]').setAttribute("aria-expanded", String(open));
    if (open) {
      renderActivity();
      loadActivity();
      $('[data-act="close-activity"]').focus();
    } else if (trigger && trigger.focus) {
      trigger.focus();
    }
  }

  /* ------------------------------------------------------ home screen tip */
  function isStandalone() {
    return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || window.navigator.standalone === true;
  }
  function renderInstall() {
    const force = new URLSearchParams(location.search).get("install");
    const show = force === "1" || (force !== "0" && !S.installDismissed && !isStandalone());
    $("#banner-install").hidden = !show;
  }
  function dismissInstall() {
    S.installDismissed = true;
    writePrefs();
    $("#banner-install").hidden = true;
    $("#main").focus();
  }

  /* ----------------------------------------------------------- summary */
  function renderSummary() {
    const rows = visibleCards();
    const counts = {};
    STAGES.forEach(st => { counts[st.id] = rows.filter(it => it.stage === st.id).length; });
    const parts = ["new", "applied", "interview", "offer"].filter(id => counts[id] > 0).map(id => counts[id] + " " + stage(id).label.toLowerCase());
    const total = rows.length;
    const waiting = S.state.inbox.length;
    let text;
    if (!total) text = hasActiveFilters() ? "Nothing matches your search just now." : "Your board is empty for now.";
    else if (!parts.length) text = total + (total === 1 ? " application" : " applications") + " on your board.";
    else text = parts.join(", ") + " — " + total + " on your board in total.";
    if (waiting) text += " " + waiting + (waiting === 1 ? " job is" : " jobs are") + " waiting for a yes or no.";
    $("#summary").textContent = text;
  }

  /* ------------------------------------------------------- dialog plumbing */
  function openDialog(id, trigger, focusEl) {
    const dlg = document.getElementById(id);
    if (!dlg) return;
    if (!dlg.open) dlg.showModal();
    S.triggers.push({ dlg: dlg, el: trigger || document.activeElement });
    const target = focusEl || dlg.querySelector("input:not([type=hidden]):not([disabled]), textarea:not([hidden]), select") ||
      dlg.querySelector("button:not([data-close])") || dlg.querySelector("button");
    if (target) target.focus();
  }
  function closeDialog(id) {
    const dlg = typeof id === "string" ? document.getElementById(id) : id;
    if (dlg && dlg.open) dlg.close();
  }
  function onDialogClosed(dlg) {
    for (let i = S.triggers.length - 1; i >= 0; i--) {
      if (S.triggers[i].dlg === dlg) {
        const t = S.triggers.splice(i, 1)[0];
        if (t.el && t.el.isConnected && t.el.focus) t.el.focus();
        break;
      }
    }
    if (dlg.id === "dlg-card") clearPopupState();
    if (dlg.id === "dlg-dup") S.dup = null;
    if (dlg.id === "dlg-add") { /* the job keeps running; the dialog shows it again when reopened */ }
  }
  $$("dialog").forEach(dlg => {
    dlg.addEventListener("close", () => onDialogClosed(dlg));
    dlg.addEventListener("click", ev => {
      if (ev.target.closest && ev.target.closest("[data-close]")) closeDialog(dlg);
    });
  });

  /* ------------------------------------------------------------ popup card */
  function findCard(code) {
    if (!S.state || !code) return null;
    const c = S.state.cards.find(x => x.code === code);
    if (c) return { card: c, inbox: false };
    const i = S.state.inbox.find(x => x.code === code);
    return i ? { card: i, inbox: true } : null;
  }

  const RESUME_DOCS = [
    { id: "resume", label: "Resume", pdf: "resume_pdf", docx: "resume_docx", folder: "Resumes" },
    { id: "letter", label: "Cover letter", pdf: "letter_pdf", docx: "letter_docx", folder: "Cover Letters" }
  ];
  function fileUrl(code, kind) { return "/api/cards/" + enc(code) + "/files/" + kind; }

  const THREAD_STATUS = {
    queued: "Writing…", reading: "Writing…", writing_resume: "Writing…", checking: "Writing…",
    needs_input: "Needs your answers", ready: "Ready to read", approved: "Approved", failed: "Stopped by an error"
  };

  // The card popup only says where the documents stand. Reading, changing and approving happen in the chat.
  function resumeBlockHTML(code, r) {
    const head = '<p class="detail__label">Your documents</p>';
    const open = label => '<div class="writer__acts"><button class="btn btn--primary" type="button" data-act="open-thread" data-code="' + esc(code) + '">' +
      icon("chat", 18) + "<span>" + label + "</span></button></div>";
    let line, label = "Open chat";
    if (!r || !r.status) {
      line = "A resume and a cover letter for this job, written from your own files. You read them in a chat with me and approve. I won't apply for you.";
      label = "Write them in a chat";
    } else if (isWorking(r)) {
      line = "Writing now. " + (r.step || "This usually takes one to three minutes.") + " You can close this and come back later.";
    } else if (r.status === "needs_input") {
      line = "I have a few questions before I can write it. Answer them in the chat.";
    } else if (r.status === "ready") {
      line = "Your resume and cover letter are ready" + (r.version ? " (version " + r.version + ")" : "") + ". Read them in the chat and approve, or ask for changes.";
    } else if (r.status === "approved") {
      line = "Approved and saved. I haven't applied for anything.";
    } else if (r.status === "failed") {
      line = friendlyError(r.error);
      label = "Open chat to try again";
    } else {
      line = "I'm not sure where this stands. Open the chat to check.";
    }
    return head + '<div class="writer"><p class="writer__lede">' + esc(line) + "</p>" + open(label) + "</div>";
  }

  function popupHTML(it, inbox) {
    const dup = inbox ? null : dupOf(it);
    const r = latestReply(it);
    const hasDeadline = (it.responses || []).some(x => validDate(x.deadline));
    const resumeCurrent = S.resumeCode === it.code ? S.resume : it.resume;
    const moves = STAGES.filter(st => st.id !== it.stage && st.id !== "new").map(st =>
      '<button class="btn ' + (st.id === "rejected" || st.id === "parked" ? "btn--danger" : "btn--outline") + '" type="button" data-act="move" data-code="' +
      esc(it.code) + '" data-stage="' + st.id + '">' + esc(st.id === "parked" ? "Not for me" : "Move to " + st.label.toLowerCase()) + "</button>").join("");
    return '<div class="detail__head"><div class="od-field od-fill">' +
        '<p class="detail__eyebrow">' + esc(it.company) + ' · <span class="code">' + esc(it.code) + "</span></p>" +
        '<h2 class="detail__title" id="card-title">' + esc(it.title) + "</h2></div>" +
        '<button class="btn btn--icon od-fixed" type="button" data-act="close-card">' + icon("x", 22) +
        '<span class="sr-only">Close the details</span></button></div>' +
      '<div class="detail__body">' +
        (dup ? '<div class="dupbanner"><span class="dupbanner__icon" aria-hidden="true">' + icon("merge", 18) + "</span>" +
          '<span class="od-fill">This may be the same job as <span class="code">' + esc(dup.card.code) + "</span> on your board.</span>" +
          '<button class="btn btn--outline btn--sm" type="button" data-act="open-dup" data-code="' + esc(it.code) +
          '" data-fid="' + esc(it.code) + ':dup" aria-haspopup="dialog">Compare</button></div>' : "") +
        '<div class="od-cluster popup__chips">' + (inbox ? "" : stageChip(it, true)) + fitBadge(it) + sourceTag(it) + aiTag(it) + resumeTag(it) + "</div>" +
        (inbox ? '<p class="inbox-item__found">' + icon("info", 15) + "<span>" + esc(it.found || "Found by your assistant") + "</span></p>" : "") +
        (showFit(it) && it.fitReason ? '<p class="detail__text">' + esc(it.fitReason) + "</p>" : "") +
        (it.stage === "parked" && it.reason ? eligLine(it.reason, false) : "") +
        '<div class="od-cluster">' + openLink(it.url, it.title, it.company, "btn btn--primary", it.code + ":link") +
          (inbox ? "" : '<button class="btn btn--outline" type="button" data-act="prepare" data-code="' + esc(it.code) + '">' +
          icon("clipboard", 18) + "<span>Prepare application</span></button>") + "</div>" +
        '<p class="addnote" id="prepare-status" aria-live="polite"></p>' +
        '<textarea class="input" id="prepare-text" rows="8" readonly hidden aria-label="Application prompt"></textarea>' +
        (r ? '<div class="detail__block"><p class="detail__label">Latest reply from ' + esc(it.company) + "</p>" + replyBlock(it) + "</div>" : "") +
        (inbox ? "" : '<div class="detail__block" id="resume-block">' + resumeBlockHTML(it.code, resumeCurrent) + "</div>") +
        '<div class="detail__cols"><div class="detail__main">' +
        '<div class="detail__block"><p class="detail__label">About this job</p>' +
          (it.description || []).map(p => '<p class="detail__text">' + esc(p) + "</p>").join("") + "</div>" +
        ((it.requirements || []).length
          ? '<div class="detail__block"><p class="detail__label">What they are asking for</p><ul class="detail__reqs">' +
            it.requirements.map(q => "<li>" + icon("check", 16) + "<span>" + esc(q) + "</span></li>").join("") + "</ul></div>"
          : "") +
        (it.action ? '<div class="detail__block"><p class="detail__label">Next step</p><p class="detail__text">' + esc(it.action) + "</p></div>" : "") +
        '<div class="detail__block"><p class="detail__label">Your notes</p>' +
          ((it.notes || []).length
            ? (it.notes || []).map(n => '<p class="detail__note"><span class="addnote">' + esc(fmt(n.date, true)) + "</span><br>" + esc(n.text) + "</p>").join("")
            : '<p class="detail__note detail__note--empty">No notes yet.</p>') +
          '<div><button class="btn btn--outline" type="button" data-act="note" data-code="' + esc(it.code) + '">' + icon("note", 18) +
          "<span>Add a note</span></button></div></div>" +
        '</div><div class="detail__side">' +
        '<div class="detail__block"><p class="detail__label">The basics</p><div class="detail__facts">' +
          fact("Location", locationOf(it)) + fact("Pay", payOf(it)) +
          fact("Posted", it.posted && it.posted.date ? (it.posted.approx ? "~" : "") + fmt(it.posted.date, true) : "Not stated") +
          fact("Added", fmt(it.added, true)) + "</div></div>" +
        ((it.timeline || []).length
          ? '<div class="detail__block"><p class="detail__label">What has happened</p><div class="timeline">' +
            it.timeline.map(e => '<div class="tl ' + timelineKind(e.label) + '"><div class="tl__rail"><span class="tl__dot">' +
              icon("info", 14) + '</span><span class="tl__line"></span></div><div class="tl__body"><p class="tl__when">' +
              fmt(e.date, true) + '</p><p class="tl__what">' + esc(e.label) + (e.by === "you" ? " (you)" : "") + "</p></div></div>").join("") +
            "</div></div>"
          : "") +
        (hasDeadline ? '<div class="detail__block"><p class="detail__label">Dates</p><div class="od-cluster">' +
          '<a class="btn btn--outline" href="/api/cards/' + enc(it.code) + '/calendar.ics" download>' + icon("calendar", 18) +
          "<span>Add to calendar</span></a></div></div>" : "") +
        (inbox
          ? '<div class="detail__block detail__block--acts"><p class="detail__label">Your choice</p><div class="od-cluster">' +
            '<button class="btn btn--primary" type="button" data-act="accept" data-code="' + esc(it.code) + '">' + icon("check", 18) +
            "<span>Add to my board</span></button>" +
            '<button class="btn btn--outline" type="button" data-act="decline" data-code="' + esc(it.code) + '">Not for me</button></div></div>'
          : '<div class="detail__block detail__block--acts"><p class="detail__label">Move this card</p><div class="detail__moves">' + moves + "</div></div>") +
        "</div></div>" +
      "</div>";
  }
  function timelineKind(label) {
    const l = String(label).toLowerCase();
    if (/offer/.test(l)) return "tl--offer";
    if (/rejection|rejected/.test(l)) return "tl--rejection";
    if (/assessment/.test(l)) return "tl--assessment";
    if (/interview/.test(l)) return "tl--interview";
    if (/info/.test(l)) return "tl--info";
    if (/applied/.test(l)) return "tl--applied";
    return "tl--added";
  }
  function fact(key, value) {
    return '<div class="od-stat fact"><span class="fact__key">' + esc(key) + '</span><span class="fact__val">' + esc(value) + "</span></div>";
  }

  function paintPopup(force) {
    if (!S.popup) return;
    const dlg = $("#dlg-card");
    const f = findCard(S.popup);
    if (!f) { closeCard(); toast("That card is no longer on your board."); return; }
    const key = JSON.stringify(f.card) + "|" + f.inbox + "|" + JSON.stringify(S.resumeCode === f.card.code ? S.resume : null);
    if (!force && key === S.popupKey && dlg.open) return;
    S.popupKey = key;
    $("#card-inner").innerHTML = popupHTML(f.card, f.inbox);
  }
  function paintResume() {
    const el = $("#resume-block");
    if (el && S.popup) el.innerHTML = resumeBlockHTML(S.popup, S.resume);
  }
  function openCard(code, trigger) {
    const f = findCard(code);
    if (!f) { toast("I can't find that card any more."); return; }
    S.popup = code;
    S.popupKey = "";
    if (S.resumeCode !== code) { S.resumeCode = code; S.resume = f.card.resume || null; }
    paintPopup(true);
    openDialog("dlg-card", trigger, $('#dlg-card [data-act="close-card"]'));
    fetchResume(code);
  }
  function closeCard() { closeDialog("dlg-card"); }
  function closeCardSilently() {
    const dlg = $("#dlg-card");
    S.popup = null;
    if (dlg && dlg.open) dlg.close();
  }
  function clearPopupState() {
    S.popup = null;
    S.popupKey = "";
    cancel("resumePoll");
  }

  async function fetchResume(code) {
    const d = await request("/api/cards/" + enc(code) + "/resume");
    if (!d || S.popup !== code) return;
    S.resumeCode = code;
    S.resume = d.resume || null;
    paintResume();
    if (isWorking(S.resume)) scheduleResumePoll(code);
  }
  function scheduleResumePoll(code) {
    schedule("resumePoll", 3000, async () => {
      if (S.popup !== code) return;
      await fetchResume(code);
      if (S.popup === code && !isWorking(S.resume)) loadState();
    });
  }
  async function resumeCall(code, body, text) {
    const d = await request("/api/cards/" + enc(code) + "/resume", { method: "POST", body: body });
    if (!d) return null;
    S.resumeCode = code;
    S.resume = d.resume || null;
    if (S.popup === code) {
      paintResume();
      if (isWorking(S.resume)) scheduleResumePoll(code);
    }
    const M = S.msgs;
    if (M.open && M.code === code) {
      // Everything the script does ends with a message in the chat, so wait for one before unlocking the box again.
      const msgs = M.chat ? M.chat.messages : [];
      M.busy = { since: Date.now(), lastTs: msgs.length ? msgs[msgs.length - 1].ts : "", userN: msgs.filter(x => x.role === "user").length,
                 text: text || "", seen: isWorking(d.resume) };
      if (M.chat) M.chat.resume = d.resume || M.chat.resume;
      paintThread();
      scheduleMsgTick(FAST_POLL);
    }
    loadState();
    return d;
  }

  async function prepareApplication(code) {
    const status = $("#prepare-status");
    const area = $("#prepare-text");
    status.textContent = "Preparing…";
    const d = await request("/api/cards/" + enc(code) + "/handover");
    if (!d) { status.textContent = ""; return; }
    const text = d.prompt || "";
    if (!text) { status.textContent = "I couldn't build the prompt for this card."; return; }
    let copied = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        copied = true;
      }
    } catch (e) { copied = false; }
    if (!copied) {
      area.value = text;
      area.hidden = false;
      area.focus();
      area.select();
      try { copied = document.execCommand("copy"); } catch (e) { copied = false; }
    }
    status.textContent = copied ? "Copied. Paste it into your assistant or your notes."
      : "Select the text below and copy it yourself.";
  }

  /* -------------------------------------------------------- review actions */
  async function reviewCodes(codes, decision, reason) {
    let last = null, done = 0;
    for (const code of codes) {
      const d = await request("/api/inbox/" + enc(code) + "/" + decision, { method: "POST", body: { reason: reason || "" } });
      if (d) { done++; last = d; }
    }
    if (done === 0) return;
    await loadState();
    if (S.popup && codes.indexOf(S.popup) > -1) closeCard();
    S.picked = [];
    if (codes.length === 1 && last) {
      toast(decision === "accept" ? "Added to your board." : "Declined.", () => undoWith(last.undo));
    } else {
      toast((decision === "accept" ? "Added " : "Declined ") + done + (done === 1 ? " job." : " jobs."));
    }
  }
  function openDecline(codes, trigger) {
    S.pending = { type: "decline", codes: codes };
    const f = codes.length === 1 ? findCard(codes[0]) : null;
    $("#dlg-decline-sub").textContent = f
      ? f.card.title + " at " + f.card.company + ". Telling me why helps me find better matches."
      : codes.length + " jobs. Telling me why helps me find better matches.";
    $("#decline-reason").value = "";
    openDialog("dlg-decline", trigger);
  }
  async function confirmDecline() {
    const p = S.pending;
    S.pending = null;
    closeDialog("dlg-decline");
    if (!p || !p.codes) return;
    await reviewCodes(p.codes, "decline", $("#decline-reason").value.trim());
  }

  async function undoWith(undo) {
    if (!undo) return;
    const body = undo.review ? { code: undo.code, review: true } : { code: undo.code, to: undo.to };
    const d = await request("/api/undo", { method: "POST", body: body });
    if (!d) return;
    toast("Put back.");
    loadState();
  }

  /* ---------------------------------------------------------- stage moves */
  function openStageSheet(code, trigger) {
    const f = findCard(code);
    if (!f) return;
    $("#dlg-stage-sub").textContent = f.card.title + " at " + f.card.company;
    $("#dlg-stage-list").innerHTML = STAGES.map(st => {
      const current = st.id === f.card.stage;
      return '<button class="opt" type="button" data-act="pick-stage" data-code="' + esc(code) + '" data-stage="' + st.id + '" aria-current="' + current + '">' +
        '<span class="opt__badge stagechip--' + st.id + '">' + icon(st.icon, 18) + "</span>" +
        '<span class="opt__text"><span>' + esc(st.label) + '</span><span class="opt__hint">' + esc(st.hint) + "</span></span>" +
        (current ? '<span class="opt__check od-fixed">' + icon("check", 18) + '<span class="sr-only">Current stage</span></span>' : "") + "</button>";
    }).join("");
    openDialog("dlg-stage", trigger);
  }
  function openActionsSheet(code, trigger) {
    const f = findCard(code);
    if (!f) return;
    const it = f.card;
    const moves = STAGES.filter(st => st.id !== it.stage && st.id !== "new").map(st => {
      const danger = st.id === "rejected" || st.id === "parked";
      return '<button class="opt' + (danger ? " opt--danger" : "") + '" type="button" data-act="move" data-code="' + esc(code) + '" data-stage="' + st.id + '">' +
        '<span class="opt__badge stagechip--' + st.id + '">' + icon(st.icon, 18) + "</span>" +
        '<span class="opt__text"><span>' + esc(st.id === "parked" ? "Not for me" : st.label) + '</span><span class="opt__hint">' + esc(st.hint) + "</span></span></button>";
    }).join("");
    $("#dlg-actions-sub").textContent = it.title + " at " + it.company;
    $("#dlg-actions-list").innerHTML = moves +
      '<button class="opt" type="button" data-act="note" data-code="' + esc(code) + '"><span class="opt__badge" style="background:var(--accent-soft);color:var(--accent)">' +
      icon("note", 18) + '</span><span class="opt__text"><span>Add a note</span><span class="opt__hint">Only you can see it</span></span></button>' +
      '<button class="opt" type="button" data-act="open-card" data-code="' + esc(code) + '"><span class="opt__badge" style="background:var(--surface-2);color:var(--text-muted)">' +
      icon("chevron-right", 18) + '</span><span class="opt__text"><span>See full details</span><span class="opt__hint">Description, timeline and notes</span></span></button>';
    openDialog("dlg-actions", trigger);
  }
  let pendingMove = null;
  function askToMove(code, toStage, trigger) {
    const f = findCard(code);
    if (!f) return;
    pendingMove = { code: code, stage: toStage };
    const st = stage(toStage);
    $("#dlg-confirm-title").textContent = "Move to " + st.label + "?";
    $("#dlg-confirm-body").textContent = f.card.title + " at " + f.card.company + " moves from " +
      stage(f.card.stage).label + " to " + st.label + ". You can undo this straight away.";
    openDialog("dlg-confirm", trigger);
  }
  async function confirmMove() {
    const p = pendingMove;
    pendingMove = null;
    closeDialog("dlg-confirm");
    if (!p) return;
    await once("move:" + p.code, async () => {
      const f = findCard(p.code);
      const name = f ? f.card.title : "The card";
      const d = await request("/api/cards/" + enc(p.code) + "/stage", { method: "POST", body: { stage: p.stage } });
      if (!d) return;
      await loadState();
      toast(name + " moved to " + stage(p.stage).label + ".", () => undoWith(d.undo));
    });
  }

  /* ------------------------------------------------------------------ notes */
  let noteCode = null;
  function openNote(code, trigger) {
    const f = findCard(code);
    if (!f) return;
    noteCode = code;
    $("#dlg-note-sub").textContent = f.card.title + " at " + f.card.company;
    $("#note-text").value = "";
    openDialog("dlg-note", trigger, $("#note-text"));
  }
  async function saveNote() {
    const text = $("#note-text").value.trim();
    if (!text) { toast("Write something first."); return; }
    const code = noteCode;
    if (!code) return;
    const d = await request("/api/cards/" + enc(code) + "/note", { method: "POST", body: { text: text } });
    if (!d) return;
    noteCode = null;
    closeDialog("dlg-note");
    await loadState();
    toast("Note saved.");
  }

  /* ------------------------------------------------------- duplicate merge */
  function dupFact(key, a, b) {
    const same = a === b;
    return '<div class="dupfact' + (same ? " dupfact--same" : "") + '">' +
      '<span class="dupfact__key">' + esc(key) + "</span>" +
      '<span class="dupfact__val">' + esc(a) + (same ? " (same)" : "") + "</span></div>";
  }
  function dupColHTML(it, other, keep) {
    return '<label class="dupcol dupcol--pick' + (keep ? " dupcol--keep" : "") + '" data-dupcol="' + esc(it.code) + '">' +
      '<span class="dupcol__flag"><input type="radio" name="dup-keep" value="' + esc(it.code) + '"' + (keep ? " checked" : "") + ">" +
        '<span class="dupcol__flagtext">' + (keep ? "Keep this one" : "Archive this one") + "</span> · " + esc(stage(it.stage).label) + "</span>" +
      '<span class="dupcol__title">' + esc(it.title) + "</span>" +
      '<span class="dupcol__company">' + esc(it.company) + ' · <span class="code">' + esc(it.code) + "</span></span>" +
      '<span class="dupcol__facts">' +
        dupFact("Location", locationOf(it), locationOf(other)) +
        dupFact("Pay", payOf(it), payOf(other)) +
        dupFact("Posted", postedOf(it).replace(/^Posted (date )?/, ""), postedOf(other).replace(/^Posted (date )?/, "")) +
        dupFact("Added", fmt(it.added, true), fmt(other.added, true)) +
        dupFact("Found through", it.source || "Found", other.source || "Found") +
        dupFact("Replies", String((it.responses || []).length), String((other.responses || []).length)) +
      "</span>" +
    "</label>";
  }
  function paintDup() {
    const d = S.dup;
    const a = findCard(d.a), b = findCard(d.b);
    if (!a || !b) { closeDialog("dlg-dup"); toast("I can't find the other card any more."); return; }
    $("#dlg-dup-sub").textContent = "Two cards look like the same opening at " + a.card.company + ".";
    $("#dlg-dup-body").innerHTML =
      '<div class="dupgrid" role="radiogroup" aria-label="Which card to keep">' +
        dupColHTML(a.card, b.card, d.keep === d.a) + dupColHTML(b.card, a.card, d.keep === d.b) + "</div>" +
      '<p class="dupnote">Merging keeps the card you pick with everything that has happened on it. The other one moves to the archive ' +
        "with a note saying where it went. Keep both if they really are different jobs.</p>";
  }
  function openDup(code, trigger) {
    const f = findCard(code);
    const other = f && dupOf(f.card);
    if (!other) { toast("I can't find the other card any more."); return; }
    // Default to keeping the older card, which usually has more history on it.
    const older = String(other.card.added || "") < String(f.card.added || "") ? other.card.code : code;
    S.dup = { a: code, b: other.card.code, keep: older };
    paintDup();
    openDialog("dlg-dup", trigger, $('#dlg-dup input[name="dup-keep"]:checked'));
  }
  function pickDupKeep(code) {
    if (!S.dup) return;
    S.dup.keep = code;
    $$("#dlg-dup [data-dupcol]").forEach(col => {
      const on = col.getAttribute("data-dupcol") === code;
      col.classList.toggle("dupcol--keep", on);
      col.querySelector(".dupcol__flagtext").textContent = on ? "Keep this one" : "Archive this one";
    });
  }
  async function mergeDup() {
    const d = S.dup;
    if (!d) return;
    const keep = d.keep, drop = keep === d.a ? d.b : d.a;
    const res = await request("/api/merge", { method: "POST", body: { keep: keep, drop: drop } });
    if (!res) return;
    S.dup = null;
    closeDialog("dlg-dup");
    if (S.popup === drop) closeCard();
    await loadState();
    toast(res.message || "Merged.");
  }
  function keepBothDup() {
    const d = S.dup;
    if (!d) return;
    S.dupKept = S.dupKept.filter(k => k !== pairKey(d.a, d.b)).concat([pairKey(d.a, d.b)]);
    writePrefs();
    S.dup = null;
    // Only the banner changes, so drop it in place instead of repainting the popup (keeps typed text).
    // Its Compare button was the focus target for closing this dialog, so hand focus to the popup instead.
    const dlg = $("#dlg-dup");
    const back = $('#dlg-card[open] [data-act="close-card"]');
    S.triggers.forEach(t => { if (t.dlg === dlg && back) t.el = back; });
    const banner = $("#card-inner .dupbanner");
    if (banner) banner.remove();
    closeDialog(dlg);
    render();
    toast("Keeping both cards.");
  }

  /* ------------------------------------------------------------ attention */
  async function attentionAction(id, kind) {
    const d = await request("/api/attention/" + enc(id) + "/" + kind, {
      method: "POST", body: kind === "snooze" ? { days: 3 } : {}
    });
    if (!d) return;
    loadState();
  }

  /* -------------------------------------------------------------- filters */
  function renderFilterSheet() {
    const f = S.draft || S.filters;
    $("#f-stages").innerHTML = STAGES.map(st =>
      '<button class="chip" type="button" data-f="stage" data-value="' + st.id + '" aria-pressed="' + (f.stages.indexOf(st.id) > -1) + '">' +
      icon(st.icon, 15) + "<span>" + esc(st.short) + "</span></button>").join("");
    $("#f-fit").innerHTML = FIT_OPTIONS.map(o =>
      '<button class="chip" type="button" data-f="fit" data-value="' + o.id + '" aria-pressed="' + (f.fit === o.id) + '">' + esc(o.label) + "</button>").join("");
    $("#f-where").innerHTML = WHERE_OPTIONS.map(o =>
      '<button class="chip" type="button" data-f="where" data-value="' + o.id + '" aria-pressed="' + (f.where === o.id) + '">' + esc(o.label) + "</button>").join("");
    $("#f-added").innerHTML = ADDED_OPTIONS.map(o =>
      '<button class="chip" type="button" data-f="added" data-value="' + o.id + '" aria-pressed="' + (f.added === o.id) + '">' + esc(o.label) + "</button>").join("");
    $("#f-lowfit").checked = f.showLow;
  }
  function openFilters(trigger) {
    S.draft = JSON.parse(JSON.stringify(S.filters));
    renderFilterSheet();
    openDialog("dlg-filters", trigger);
  }

  /* -------------------------------------------------------------------- add */
  function renderAdd() {
    const a = S.add;
    const body = $("#dlg-add-body");
    const foot = $("#dlg-add-foot");
    if (a.mode === "input") {
      $("#dlg-add-sub").textContent = "Paste one posting, several, or a company careers page.";
      body.innerHTML = '<div class="addbox od-stack" style="--od-gap:var(--sp-3)">' +
        '<div class="od-field field" style="--od-gap:6px"><label class="field__label" for="add-text">Job links</label>' +
        '<textarea class="input" id="add-text" placeholder="https://…">' + esc(a.text) + "</textarea>" +
        '<p class="field__help">One link per line. Up to 10 at a time.</p></div>' +
        '<p class="addnote">I open each link, check it against your rules, and add the ones that pass. Nothing is applied for you.</p></div>';
      foot.innerHTML = '<button class="btn btn--outline" type="button" data-close>Cancel</button>' +
        '<button class="btn btn--primary" type="button" data-act="add-start"' + (a.text.trim() ? "" : " disabled") + ">Check links</button>";
      return;
    }
    if (a.mode === "working" && a.job) {
      $("#dlg-add-sub").textContent = "This can take a minute for each link. You can close this and come back.";
      body.innerHTML = '<h3 class="filterset__label">Reading your links</h3><ul class="proglist">' + a.job.items.map(it => {
        const done = it.state === "done";
        const cls = done ? "prog--done" : it.state === "working" ? "prog--working" : "";
        const st = done ? "Done" : it.state === "working" ? "Reading…" : "Waiting";
        return '<li class="prog ' + cls + '"><span class="prog__mark">' + icon(done ? "check" : it.state === "working" ? "refresh" : "clock", 14) + "</span>" +
          '<span class="prog__label">' + esc(it.label) + '</span><span class="prog__state">' + st + "</span></li>";
      }).join("") + "</ul>";
      foot.innerHTML = '<button class="btn btn--outline" type="button" data-act="add-stop">Stop</button><button class="btn btn--quiet" type="button" data-close>Close</button>';
      return;
    }
    // results
    const job = a.job || { items: [] };
    $("#dlg-add-sub").textContent = job.state === "error" ? "Something went wrong." : "Here is what I found.";
    const picks = [];
    const out = (job.items || []).map(it => resultHTML(job, it, picks)).join("");
    body.innerHTML = (job.state === "error" ? '<p class="bubble bubble--error">' + esc(job.error || "Something went wrong while reading the links.") + "</p>" : "") +
      out + (picks.length ? '<div class="od-stack" style="--od-gap:var(--sp-3)">' +
        '<label class="switch"><input type="checkbox" id="pick-anyway"' + (a.anyway ? " checked" : "") + "><span>Include the ones marked not eligible</span></label>" +
        '<ul class="picklist">' + picks.join("") + "</ul>" +
        '<div class="od-cluster"><button class="btn btn--primary" type="button" data-act="pick-confirm" data-job="' + esc(job.id) + '">Add the selected jobs</button></div></div>' : "");
    foot.innerHTML = '<button class="btn btn--outline" type="button" data-act="add-reset">Check more links</button>' +
      '<button class="btn btn--primary" type="button" data-close>Done</button>';
  }
  function resultHTML(job, it, picks) {
    if (it.verdict === "listing" && it.listing) {
      const jobs = it.listing.jobs || [];
      jobs.forEach(j => {
        const sel = S.add.picks.length ? S.add.picks.indexOf(j.id) > -1 : !!j.eligible;
        const allowed = j.eligible || S.add.anyway;
        picks.push('<li class="pick' + (j.eligible ? "" : " pick--off") + '"><input type="checkbox" data-pick="' + esc(j.id) + '" id="pk-' + esc(j.id) + '"' +
          (sel && allowed ? " checked" : "") + (allowed ? "" : " disabled") + '><label class="pick__text" for="pk-' + esc(j.id) + '">' +
          '<span class="pick__title">' + esc(j.title) + "</span>" +
          '<span class="pick__team">' + esc([j.team, j.location].filter(Boolean).join(" · ")) + "</span>" +
          (j.eligible ? "" : '<span class="pick__why">' + esc(j.reason || "Marked not eligible") + "</span>") + "</label></li>");
      });
      return '<article class="result"><p class="review__sum">' + esc(it.headline) + "</p>" +
        '<p class="review__src">' + esc(it.listing.source || "") + " · " + esc(it.listing.found) + " openings</p></article>";
    }
    const parts = ['<article class="result"><p class="review__sum">' + esc(it.headline || "Done") + "</p>"];
    if (it.item) {
      parts.push('<ul class="result__meta"><li>' + icon("briefcase", 15) + "<span>" + esc(it.item.title) + " at " + esc(it.item.company) + "</span></li>" +
        (it.item.pay ? "<li>" + icon("wallet", 15) + "<span>" + esc(it.item.pay) + "</span></li>" : "") +
        '<li>' + icon("pin", 15) + "<span>" + esc([it.item.workStyle, it.item.city].filter(Boolean).join(" · ") || "Location not stated") + "</span></li></ul>");
    }
    if (it.note) parts.push('<p class="addnote">' + esc(it.note) + "</p>");
    if (it.verdict === "ineligible") {
      parts.push(eligLine(it.eligibility || it.headline || "Not eligible", false));
      parts.push('<div class="od-cluster"><button class="btn btn--outline btn--sm" type="button" data-act="add-anyway" data-job="' + esc(job.id) +
        '" data-n="' + esc(it.n) + '">Add anyway</button></div>');
    }
    if ((it.verdict === "added" || it.verdict === "tracked") && it.code) {
      parts.push('<div class="od-cluster"><button class="btn btn--outline btn--sm" type="button" data-act="open-card" data-code="' + esc(it.code) + '">Open on my board</button></div>');
    }
    parts.push("</article>");
    return parts.join("");
  }
  function readPicks() {
    const checked = $$("#dlg-add-body input[data-pick]:checked").map(i => i.getAttribute("data-pick"));
    S.add.picks = checked;
  }
  function openAdd(trigger) {
    if (S.add.mode === "working" && !S.add.job) S.add.mode = "input";
    renderAdd();
    openDialog("dlg-add", trigger);
  }
  async function startAdd() {
    const text = S.add.text.trim();
    if (!text) return;
    await once("add-start", async () => {
      const job = await request("/api/add-links", { method: "POST", body: { text: text } });
      if (!job) return;
      S.add = { mode: "working", job: job, text: text, anyway: false, picks: [] };
      renderAdd();
      pollAdd(job.id);
    });
  }
  function pollAdd(id) {
    schedule("addPoll", 1500, async () => {
      const d = await request("/api/add-links/" + enc(id));
      if (!d) {
        if (S.add.job && S.add.job.id === id) { S.add.mode = "results"; S.add.job = { id: id, state: "error", error: NET_MSG, items: [] }; renderAdd(); }
        return;
      }
      if (!S.add.job || S.add.job.id !== id) return;
      S.add.job = d;
      if (d.state === "working") {
        S.add.mode = "working";
        renderAdd();
        pollAdd(id);
        return;
      }
      S.add.mode = "results";
      renderAdd();
      loadState();
    });
  }
  async function addAction(path, body, resultMode) {
    const d = await request(path, { method: "POST", body: body || {} });
    if (!d) return null;
    if (d.state === "working") {
      S.add.job = d;
      S.add.mode = "working";
      renderAdd();
      pollAdd(d.id);
    } else {
      S.add.job = d;
      S.add.mode = "results";
      renderAdd();
      loadState();
    }
    return d;
  }

  /* ----------------------------------------------------------------- chat */
  function richText(text) {
    const known = knownCodes();
    return String(text || "").split(/\n+/).filter(Boolean).map(line => {
      const parts = esc(line).split(/(\[[0-9A-Fa-f]{4,6}\])/).map(part => {
        const m = part.match(/^\[([0-9A-Fa-f]{4,6})\]$/);
        if (m && known.has(m[1].toUpperCase())) {
          return '<button class="chip chip--code" type="button" data-act="open-card" data-code="' + esc(m[1].toUpperCase()) + '">[' +
            esc(m[1].toUpperCase()) + "]</button>";
        }
        return part;
      });
      return "<p>" + parts.join("") + "</p>";
    }).join("");
  }
  function chatBubbleHTML(m) {
    if (m.role === "user") return '<div class="bubble bubble--me"><p>' + esc(m.text) + "</p></div>";
    if (m.role === "working") {
      return '<div class="bubble bubble--bot" role="status"><p class="bubble__who">Assistant</p><div class="bubble__work">' +
        '<span class="bubble__dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Looking into it…</span>' +
        '<button class="btn btn--quiet btn--sm" type="button" data-act="chat-stop" data-id="' + esc(m.jobId) + '">Stop</button></div></div>';
    }
    if (m.role === "error") return '<div class="bubble bubble--error" role="alert"><p class="bubble__who">Assistant</p><p>' + esc(m.text) + "</p></div>";
    if (m.role === "note") return '<p class="addnote">' + esc(m.text) + "</p>";
    return '<div class="bubble bubble--bot"><p class="bubble__who">Assistant</p>' + richText(m.text) + "</div>";
  }
  function renderChat() {
    const log = $("#chat-log");
    log.innerHTML = S.chat.length
      ? S.chat.map(chatBubbleHTML).join("")
      : '<p class="addnote">Ask about your applications, replies or deadlines. I can also check your email.</p>';
    log.scrollTop = log.scrollHeight;
    $("#chat-chips").innerHTML = SUGGESTIONS.map(q =>
      '<button class="chip" type="button" data-act="chat-suggest" data-q="' + esc(q) + '">' + esc(q) + "</button>").join("");
    $("#chat-send").disabled = !!S.chatJob;
  }
  async function loadChatHistory() {
    if (S.chatLoaded) return;
    const d = await request("/api/chat/history");
    if (!d) return;
    S.chatLoaded = true;
    const past = (d.items || []).map(m => ({ role: m.role === "user" ? "user" : "assistant", text: m.text || "" }));
    S.chat = past.concat(S.chat);
    renderChat();
  }
  // Where focus goes when the assistant closes: the button that opened it, or the nearest thing that still works.
  function chatReturn() {
    const t = S.chatTrigger;
    if (t && t.isConnected && !t.closest("[inert]") && t.getClientRects().length) return t;
    return S.msgs.open ? $("#msgs-back") : $(".chat-fab");
  }
  function setChatOpen(open, trigger) {
    const aside = $("#chat");
    if (open && S.activityOpen) setActivityOpen(false);
    aside.hidden = !open;
    $("#view-app").classList.toggle("has-chat", open);
    $$('[data-act="open-chat"]').forEach(b => b.setAttribute("aria-expanded", String(open)));
    if (open) {
      loadChatHistory();
      renderChat();
      $("#chat-input").focus();
    } else if (trigger && trigger.focus) {
      trigger.focus();
    }
  }
  async function sendChat(text) {
    text = String(text || "").trim();
    if (!text || S.chatJob) return;
    const job = await request("/api/chat", { method: "POST", body: { text: text } });
    if (!job) return;
    S.chat = S.chat.concat([{ role: "user", text: text }, { role: "working", jobId: job.id }]);
    S.chatJob = job.id;
    $("#chat-input").value = "";
    renderChat();
    pollChat(job.id);
  }
  function pollChat(id) {
    schedule("chatPoll", 1500, async () => {
      const d = await request("/api/chat/" + enc(id));
      const i = S.chat.findIndex(m => m.role === "working" && m.jobId === id);
      if (!d) {
        if (i > -1) S.chat.splice(i, 1, { role: "error", text: NET_MSG });
        S.chatJob = null;
        renderChat();
        return;
      }
      if (d.state === "working") { pollChat(id); return; }
      const repl = d.state === "done" ? { role: "assistant", text: d.text || "I don't have an answer for that." }
        : d.state === "cancelled" ? { role: "note", text: "Stopped." }
        : { role: "error", text: d.error || "The assistant didn't answer. Try again." };
      if (i > -1) S.chat.splice(i, 1, repl); else S.chat.push(repl);
      S.chatJob = null;
      renderChat();
    });
  }
  async function stopChat(id) {
    await request("/api/chat/" + enc(id) + "/cancel", { method: "POST", body: {} });
  }

  /* ------------------------------------------------------------- messages */
  // A full-page screen at #/messages (the list) and #/messages/<CODE> (one job's chat with the resume agent).
  // The server owns the conversation; this screen polls it, so it survives a reload or a closed tab.
  const MSG_ROUTE = /^#\/messages(?:\/([0-9A-Za-z]{1,12}))?$/;
  const FAST_POLL = 2000, SLOW_POLL = 8000, BUSY_GIVE_UP = 15000;

  // Replaces an element's HTML only when it changed, so focus, scroll and a loaded PDF are left alone.
  function paintInto(el, html) {
    if (!el || el.__h === html) return false;
    el.__h = html;
    el.innerHTML = html;
    return true;
  }
  function msgRoute() {
    const m = MSG_ROUTE.exec(location.hash);
    return m ? { code: (m[1] || "").toUpperCase() } : null;
  }
  function goMessages(code) {
    location.hash = "#/messages" + (code ? "/" + code : "");
    route();
  }
  function leaveMessages() {
    history.pushState(null, "", location.pathname + location.search);
    route();
  }
  function route() {
    if (S.ended || !S.state) return;
    const r = msgRoute();
    if (r) showMessages(r.code); else hideMessages();
  }
  function setBehindInert(on) {
    [".topbar", "#banners", "#main"].forEach(sel => { const el = $(sel); if (el) el.inert = on; });
  }
  function shortAgo(ts) {
    const t = Date.parse(ts);
    if (!t) return "";
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "now";
    if (s < 3600) return Math.floor(s / 60) + " min";
    if (s < 86400) return Math.floor(s / 3600) + " h";
    if (s < 86400 * 7) return Math.floor(s / 86400) + " d";
    return fmt(new Date(t).toISOString().slice(0, 10));
  }
  function clock(ts) {
    const t = Date.parse(ts);
    return t ? new Date(t).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  }
  function msgWorking() {
    const M = S.msgs;
    return !!(M.busy || (M.chat && isWorking(M.chat.resume)));
  }
  function threadMeta(code) {
    const f = findCard(code);
    const t = S.msgs.threads.find(x => x.code === code);
    return { company: (f && f.card.company) || (t && t.company) || "", title: (f && f.card.title) || (t && t.title) || "" };
  }
  function paintBadge() {
    const n = S.state ? S.state.messagesUnread || 0 : 0;
    const b = $("#msgs-badge");
    b.hidden = !n;
    b.textContent = n > 99 ? "99+" : String(n);
    $("#msgs-label").textContent = n ? "Messages, " + n + " unread" : "Messages";
  }

  function showMessages(code) {
    const M = S.msgs;
    if (!M.open) {
      if (S.activityOpen) setActivityOpen(false);
      if (!$("#chat").hidden) setChatOpen(false);
      M.open = true;
      $("#msgs").hidden = false;
      $("#view-app").classList.add("has-msgs");
      setBehindInert(true);
      $('[data-act="open-messages"]').setAttribute("aria-expanded", "true");
      fetchThreads();
      scheduleMsgTick(SLOW_POLL);
    }
    if (code) {
      if (code !== M.code) openThread(code);
    } else if (M.code || M.screen !== "list") {
      M.code = "";
      M.chat = null;
      M.busy = null;
      M.screen = "list";
      paintMsgs();
    } else {
      paintMsgs();
    }
  }
  function hideMessages() {
    const M = S.msgs;
    if (!M.open) return;
    M.open = false;
    M.code = "";
    M.chat = null;
    M.busy = null;
    M.threads = [];
    M.loaded = false;
    cancel("msgTick");
    $("#msgs").hidden = true;
    $("#view-app").classList.remove("has-msgs");
    setBehindInert(false);
    paintInto($("#msgs-preview"), "");     // unload the PDF
    paintInto($("#msgs-chat"), "");
    $("#msgs-chat").removeAttribute("data-code");
    const b = $('[data-act="open-messages"]');
    b.setAttribute("aria-expanded", "false");
    b.focus();
    loadState();
  }
  function openThread(code) {
    const M = S.msgs;
    const f = findCard(code);
    if (!f || f.inbox) {
      toast(f ? "Add this job to your board first, then I can write for it." : "I can't find that job any more.");
      history.replaceState(null, "", location.pathname + location.search + "#/messages");
      M.code = "";
      M.screen = "list";
      paintMsgs();
      return;
    }
    M.code = code;
    M.chat = null;
    M.busy = null;
    M.doc = "resume";
    M.announcedCode = "";
    M.screen = "chat";
    M.refocus = false;
    paintMsgs();
    fetchChat(code, true);
    scheduleMsgTick(SLOW_POLL);
    if (!window.matchMedia("(pointer: coarse)").matches) {
      const ta = $("#msg-input");
      if (ta) ta.focus();
    }
  }

  async function fetchThreads() {
    const d = await request("/api/messages");
    if (!d) return;
    const M = S.msgs;
    M.threads = Array.isArray(d.threads) ? d.threads : [];
    M.loaded = true;
    if (S.state) S.state.messagesUnread = d.unread || 0;
    paintBadge();
    if (M.open) paintList();
  }
  async function fetchChat(code, first) {
    const M = S.msgs, seq = M.seq = (M.seq || 0) + 1;
    const d = await request("/api/cards/" + enc(code) + "/resume/chat");
    if (!d || !M.open || M.code !== code || seq !== M.seq) return;   // a newer fetch is already on its way
    const messages = Array.isArray(d.messages) ? d.messages : [];
    M.chat = { messages: messages, resume: d.resume || null };
    if (M.busy) {
      const last = messages.length ? messages[messages.length - 1] : null;
      const working = isWorking(M.chat.resume);
      if (working) M.busy.seen = true;
      const answered = !!last && last.role !== "user" && last.ts !== M.busy.lastTs;
      const gaveUp = Date.now() - M.busy.since > BUSY_GIVE_UP;
      if (!working && (M.busy.seen || answered || gaveUp)) {
        if (gaveUp && !M.busy.seen && !answered) toast("That didn't go through. Try again in a moment.");
        M.busy = null;
      }
    }
    if (S.resumeCode === code) S.resume = M.chat.resume;
    paintThread();
    const row = M.threads.find(t => t.code === code), rs = M.chat.resume;
    if (row && rs && (row.status !== rs.status || row.version !== (rs.version || 0))) fetchThreads();   // keep the list beside the chat current
    if (!document.hidden && !M.marking && messages.some(m => m.role !== "user" && !m.quiet && String(m.ts || "") > String(d.readAt || ""))) {
      M.marking = true;
      const ok = await request("/api/cards/" + enc(code) + "/resume/read", { method: "POST", body: {} });
      M.marking = false;
      if (ok) fetchThreads();
    }
    // A job with no documents yet starts its first draft the moment you open its chat. An existing chat never rewrites itself.
    if (first && M.code === code && !M.chat.resume && !M.busy && !messages.length) startDraft(code);
  }
  function scheduleMsgTick(ms) {
    schedule("msgTick", ms, async () => {
      const M = S.msgs;
      if (!M.open) return;
      if (!document.hidden) {
        M.tick = (M.tick || 0) + 1;
        const jobs = [];
        if (M.code) jobs.push(fetchChat(M.code, false));
        if (!msgWorking() || M.tick % 4 === 0) jobs.push(fetchThreads());
        await Promise.all(jobs);
      }
      if (S.msgs.open) scheduleMsgTick(msgWorking() ? FAST_POLL : SLOW_POLL);
    });
  }

  async function startDraft(code) {
    const M = S.msgs;
    M.busy = { since: Date.now(), lastTs: "", userN: 0, text: "", seen: false };
    paintThread();
    const d = await once("resume:" + code, () => resumeCall(code, {}));
    if (!d && M.code === code) { M.busy = null; paintThread(); }
  }
  async function sendMsg() {
    const M = S.msgs, code = M.code, ta = $("#msg-input");
    if (!code || !ta || msgWorking()) return;
    const text = ta.value.trim();
    if (!text) { ta.focus(); return; }
    const d = await once("resume:" + code, () => resumeCall(code, { message: text }, text));
    if (d && M.code === code) { ta.value = ""; M.refocus = true; paintCompose(); }
  }
  async function stopRun(code) {
    await request("/api/cards/" + enc(code) + "/resume", { method: "POST", body: { stop: true } });
    scheduleMsgTick(700);
  }
  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        toast("Copied.");
        return;
      }
    } catch (e) { /* fall through to showing it */ }
    toast("Copy this: " + text);
  }

  /* ---- painting ---- */
  function paintMsgs() {
    const M = S.msgs;
    $("#msgs").setAttribute("data-screen", M.screen);
    paintList();
    const chat = $("#msgs-chat");
    if (!M.code) {
      if (chat.getAttribute("data-code") !== "") {
        chat.setAttribute("data-code", "");
        paintInto(chat, '<div class="msgc__empty"><p class="addnote">Pick a conversation on the left. Each job you ask a resume for has its own chat.</p></div>');
      }
    } else if (chat.getAttribute("data-code") !== M.code) {
      chat.setAttribute("data-code", M.code);
      chat.__h = undefined;
      chat.innerHTML = chatSkeleton();
    }
    paintThread();
    paintPreview();
    if (!M.code && !window.matchMedia("(pointer: coarse)").matches) $("#msgs-back").focus();
  }
  function chatSkeleton() {
    return '<div class="msgc__head" id="msg-head"></div>' +
      '<div class="msgc__log" id="msg-log" role="region" tabindex="-1" aria-label="Conversation with the resume agent"></div>' +
      '<div class="sr-only" id="msg-announce" role="status" aria-live="polite"></div>' +
      '<form class="msgc__compose" id="msg-compose" novalidate>' +
        '<label class="sr-only" for="msg-input">Message the resume agent</label>' +
        '<textarea class="input" id="msg-input" rows="2" maxlength="2000"></textarea>' +
        '<div class="msgc__btns">' +
          '<button class="btn btn--primary" type="submit" id="msg-send">' + icon("send", 18) + "<span>Send</span></button>" +
          '<button class="btn btn--outline" type="button" data-act="resume-stop" id="msg-stop" hidden>Stop</button>' +
        "</div></form>";
  }
  function paintList() {
    const M = S.msgs;
    if (!M.open) return;
    const q = M.query.trim().toLowerCase();
    let rows = M.threads.slice();
    if (M.code && !rows.some(t => t.code === M.code)) {
      const meta = threadMeta(M.code), r = M.chat && M.chat.resume;
      rows.unshift({ code: M.code, company: meta.company, title: meta.title, status: (r && r.status) || "", unread: 0, snippet: "", from: "", ts: "" });
    }
    if (q) rows = rows.filter(t => (t.company + " " + t.title + " " + t.code + " " + t.snippet).toLowerCase().indexOf(q) > -1);
    if (M.unreadOnly) rows = rows.filter(t => t.unread > 0);
    const none = M.threads.length || M.code
      ? "No conversations match."
      : M.loaded ? 'No resume chats yet. Open a job and choose "Write them in a chat".' : "Loading…";
    paintInto($("#msgs-threads"), rows.length ? rows.map(threadRowHTML).join("") : '<p class="addnote msgs__none">' + none + "</p>");
    const chip = $("#msgs-unread-only");
    chip.setAttribute("aria-pressed", String(M.unreadOnly));
  }
  function threadRowHTML(t) {
    const on = t.code === S.msgs.code;
    const done = t.status === "ready" || t.status === "approved";
    return '<a class="threadrow' + (on ? " is-on" : "") + (t.unread ? " is-unread" : "") + '" href="#/messages/' + esc(t.code) + '"' + (on ? ' aria-current="page"' : "") + ">" +
      '<span class="threadrow__top"><strong class="threadrow__co">' + esc(t.company || "Job") + '</strong><span class="threadrow__when">' + esc(shortAgo(t.ts)) + "</span></span>" +
      '<span class="threadrow__title">' + esc(t.title) + ' <span class="code">' + esc(t.code) + "</span></span>" +
      (t.snippet ? '<span class="threadrow__snip">' + (t.from === "you" ? "You: " : "") + esc(t.snippet) + "</span>" : "") +
      '<span class="threadrow__foot">' + (THREAD_STATUS[t.status] ? '<span class="tag' + (done ? " tag--ready" : "") + '">' + esc(THREAD_STATUS[t.status]) + "</span>" : "") +
      (t.unread ? '<span class="count-dot" aria-hidden="true">' + t.unread + '</span><span class="sr-only">' + t.unread + " unread</span>" : "") + "</span></a>";
  }

  function paintThread() {
    const M = S.msgs;
    const chat = $("#msgs-chat");
    if (!M.open || !M.code || chat.getAttribute("data-code") !== M.code) return;
    paintInto($("#msg-head"), msgHeadHTML());
    const log = $("#msg-log");
    const first = log.__h === undefined;
    const near = log.scrollHeight - log.scrollTop - log.clientHeight < 140;
    const sig = el => (el.getAttribute("data-act") || "") + "|" + (el.getAttribute("data-doc") || "") + "|" + (el.getAttribute("href") || "");
    const held = document.activeElement && log.contains(document.activeElement) ? sig(document.activeElement) : "";
    if (paintInto(log, msgLogHTML())) {
      if (first || near) log.scrollTop = log.scrollHeight;
      if (held) {
        const again = $$("button, a", log).find(el => sig(el) === held);
        if (again) again.focus({ preventScroll: true });
      }
    }
    paintCompose();
    paintPreview();
    paintList();
    // Screen readers hear only the newest agent message, not the whole history again.
    const say = $("#msg-announce");
    if (say && M.chat) {
      const msgs = M.chat.messages, last = msgs.length ? msgs[msgs.length - 1] : null;
      const mark = last ? String(last.ts) : "";
      if (M.announcedCode !== M.code) { M.announcedCode = M.code; M.announced = mark; }      // what was already there is not news
      else if (mark !== M.announced) {
        M.announced = mark;
        if (last && last.role !== "user" && !last.quiet) say.textContent = "Resume agent: " + last.text;
      }
    }
  }
  function paintCompose() {
    const M = S.msgs;
    const ta = $("#msg-input");
    if (!ta) return;
    const working = msgWorking();
    const st = M.chat && M.chat.resume && M.chat.resume.status;
    ta.disabled = working;
    ta.placeholder = working ? "I'm working on it. You can write again when I'm done."
      : st === "needs_input" ? "Type your answers here"
      : st === "ready" || st === "approved" ? 'Ask for a change, like "make the summary shorter"'
      : "Write a message";
    $("#msg-send").disabled = working;
    $("#msg-stop").hidden = !working || st === "checking";   // writing the files can't be interrupted halfway
    if (!working && M.refocus) {
      M.refocus = false;
      if (!window.matchMedia("(pointer: coarse)").matches && !document.activeElement.closest("dialog[open]")) ta.focus();
    }
  }
  function msgHeadHTML() {
    const M = S.msgs, code = M.code, meta = threadMeta(code);
    const r = M.chat && M.chat.resume, st = r && r.status;
    const hasFiles = !!(r && r.files && (r.files.resume_pdf || r.files.letter_pdf));
    let act = "";
    if (st === "ready") {
      act = '<button class="btn btn--primary btn--sm" type="button" data-act="resume-approve" data-code="' + esc(code) + '"' + (msgWorking() ? " disabled" : "") + ">" +
        icon("check", 16) + "<span>Approve</span></button>";
    } else if (st === "approved") {
      act = '<span class="tag tag--ready">' + icon("check", 14) + "<span>Approved</span></span>";
    }
    return '<button class="btn btn--quiet btn--sm only-phone od-fixed" type="button" data-act="msgs-list">' + icon("chevron-left", 16) + "<span>Chats</span></button>" +
      '<div class="od-field od-fill msgc__who"><h3 class="msgc__title" id="msg-title" tabindex="-1">' + esc(meta.company) + ' <span class="code">' + esc(code) + "</span></h3>" +
      '<p class="msgc__sub">' + esc(meta.title) + (r && r.version ? " · version " + r.version : "") + "</p></div>" +
      '<div class="msgc__acts">' +
        (hasFiles ? '<button class="btn btn--outline btn--sm only-narrow" type="button" data-act="msgs-pane" data-pane="preview">' + icon("file", 16) + "<span>Documents</span></button>" : "") +
        '<button class="btn btn--quiet btn--sm" type="button" data-act="open-card" data-code="' + esc(code) + '">Job details</button>' + act +
      "</div>";
  }
  function progListHTML(status) {
    const order = Object.keys(WORKING);
    const at = order.indexOf(status);
    return '<ul class="proglist">' + order.map((id, i) => {
      const done = i < at, working = i === at;
      return '<li class="prog' + (done ? " prog--done" : working ? " prog--working" : "") + '">' +
        '<span class="prog__mark">' + icon(done ? "check" : working ? "refresh" : "clock", 14) + "</span>" +
        '<span class="prog__label">' + esc(WORKING[id]) + "</span>" +
        '<span class="prog__state">' + (done ? "Done" : working ? "Working" : "Waiting") + "</span></li>";
    }).join("") + "</ul>";
  }
  function plainLines(text) {
    return String(text || "").split(/\n+/).filter(Boolean).map(l => "<p>" + esc(l) + "</p>").join("");
  }
  function msgLogHTML() {
    const M = S.msgs, c = M.chat;
    if (!c) return '<p class="addnote">Loading the conversation…</p>';
    const r = c.resume, parts = [];
    c.messages.forEach(m => parts.push(msgBubbleHTML(m, r)));
    const userN = c.messages.filter(m => m.role === "user").length;
    if (M.busy && M.busy.text && userN <= M.busy.userN) parts.push('<div class="bubble bubble--me">' + plainLines(M.busy.text) + "</div>");
    if (msgWorking()) {
      parts.push('<div class="bubble bubble--bot" role="status"><p class="bubble__who">Resume agent</p>' +
        (r && isWorking(r)
          ? progListHTML(r.status)
          : '<div class="bubble__work"><span class="bubble__dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Getting started…</span></div>') +
        "</div>");
    } else if (r && r.status === "failed") {
      parts.push('<div class="writer__acts"><button class="btn btn--primary" type="button" data-act="resume-retry" data-code="' + esc(M.code) + '">' +
        icon("refresh", 18) + "<span>Try again</span></button></div>");
    }
    if (!parts.length) parts.push('<p class="addnote">Nothing here yet.</p>');
    return parts.join("");
  }
  function msgBubbleHTML(m, r) {
    if (m.role === "user") return '<div class="bubble bubble--me">' + plainLines(m.text) + "</div>";
    if (m.quiet) return '<p class="msg-note">' + esc(m.text) + "</p>";
    return '<div class="msgb"><div class="bubble bubble--bot"><p class="bubble__who">Resume agent <span class="bubble__when">' + esc(clock(m.ts)) + "</span></p>" +
      richText(m.text) + "</div>" + (typeof m.version === "number" ? fileCardHTML(S.msgs.code, m.version, r) : "") + "</div>";
  }
  function fileCardHTML(code, version, r) {
    const files = (r && r.files) || {};
    const live = !!r && r.version === version && (files.resume_pdf || files.letter_pdf || files.resume_docx || files.letter_docx);
    if (!live) {
      return '<p class="filecard filecard--old">' + icon("file", 16) + "<span>Version " + version +
        (r && r.version > version ? ", replaced by version " + r.version : "") + "</span></p>";
    }
    const dl = (doc, kind, name, what) => '<a class="btn btn--quiet btn--sm" href="' + fileUrl(code, kind) + '" download="' + esc(name) + '">' +
      icon("download", 16) + "<span>" + what + '</span><span class="sr-only"> of the ' + esc(doc.label.toLowerCase()) + "</span></a>";
    const rows = RESUME_DOCS.map(doc => {
      const pdf = files[doc.pdf], docx = files[doc.docx];
      if (!pdf && !docx) return "";
      return '<div class="filecard__row">' + icon("file", 18) + '<span class="filecard__name">' + esc(doc.label) + "</span>" +
        '<span class="filecard__acts">' +
          (pdf ? '<button class="btn btn--outline btn--sm" type="button" data-act="msgs-doc" data-doc="' + doc.id + '">Open<span class="sr-only"> the ' + esc(doc.label.toLowerCase()) + "</span></button>" : "") +
          (pdf ? dl(doc, doc.pdf, pdf, "PDF") : "") + (docx ? dl(doc, doc.docx, docx, "Word") : "") +
        "</span></div>";
    }).join("");
    const undo = r.canUndo
      ? '<div class="filecard__undo"><button class="btn btn--quiet btn--sm" type="button" data-act="resume-undo" data-code="' + esc(code) + '"' + (msgWorking() ? " disabled" : "") + ">" +
        icon("undo", 16) + "<span>Undo that change</span></button><span class=\"addnote\">Goes back to version " + r.undoTo + ".</span></div>"
      : "";
    return '<div class="filecard"><p class="filecard__ver">Version ' + version + "</p>" + rows + undo + "</div>";
  }

  function paintPreview() {
    const M = S.msgs, el = $("#msgs-preview");
    if (!M.open || !M.code) { paintInto(el, ""); return; }
    const r = M.chat && M.chat.resume, files = (r && r.files) || {};
    let doc = RESUME_DOCS.find(d => d.id === M.doc) || RESUME_DOCS[0];
    if (!files[doc.pdf]) doc = RESUME_DOCS.find(d => files[d.pdf]) || doc;
    const pdf = files[doc.pdf];
    if (!pdf) {
      paintInto(el, '<div class="prev__head"><button class="btn btn--quiet btn--sm only-narrow" type="button" data-act="msgs-pane" data-pane="chat">' +
        icon("chevron-left", 16) + "<span>Back to chat</span></button></div>" +
        '<div class="prev__empty"><p class="addnote">Your resume and cover letter will show up here once they are written.</p></div>');
      return;
    }
    const url = fileUrl(M.code, doc.pdf) + "?v=" + (r.version || 0);
    // Android Chrome can't show a PDF inside a page and iPhones show only the first page, so phones get a link.
    const inline = navigator.pdfViewerEnabled !== false && !window.matchMedia("(max-width: 767px), (pointer: coarse)").matches;
    const docx = files[doc.docx];
    const path = doc.folder + "/" + pdf;
    const tabs = RESUME_DOCS.filter(d => files[d.pdf]).map(d =>
      '<button class="segmented__btn' + (d.id === doc.id ? " is-on" : "") + '" type="button" data-act="msgs-doc" data-doc="' + d.id + '" aria-pressed="' + (d.id === doc.id) + '">' +
      "<span>" + esc(d.label) + "</span></button>").join("");
    paintInto(el,
      '<div class="prev__head"><button class="btn btn--quiet btn--sm only-narrow od-fixed" type="button" data-act="msgs-pane" data-pane="chat">' +
        icon("chevron-left", 16) + "<span>Back to chat</span></button>" +
        '<div class="segmented" role="group" aria-label="Choose a document">' + tabs + "</div></div>" +
      '<div class="prev__body">' + (inline
        ? '<iframe class="resume-frame" title="' + esc(doc.label) + ' preview" src="' + url + '"></iframe>'
        : '<div class="doc-fallback"><p class="addnote">Open the PDF in its own tab to read every page. Some phones download it instead.</p></div>') + "</div>" +
      '<div class="prev__foot"><p class="prev__path addnote">Saved as <span class="code">' + esc(path) + "</span>" +
        '<button class="btn btn--quiet btn--sm" type="button" data-act="copy-path" data-path="' + esc(path) + '">' + icon("copy", 16) + "<span>Copy</span></button></p>" +
        '<div class="prev__acts">' +
          '<a class="btn btn--primary btn--sm" href="' + url + '" target="_blank" rel="noopener">' + icon("external", 16) + '<span>Open in new tab</span><span class="sr-only"> (opens in a new tab)</span></a>' +
          '<a class="btn btn--outline btn--sm" href="' + fileUrl(M.code, doc.pdf) + '" download="' + esc(pdf) + '">' + icon("download", 16) + "<span>PDF</span></a>" +
          (docx ? '<a class="btn btn--outline btn--sm" href="' + fileUrl(M.code, doc.docx) + '" download="' + esc(docx) + '">' + icon("download", 16) + "<span>Word</span></a>" : "") +
        "</div></div>");
  }

  /* ----------------------------------------------------------- theme, view */
  function applyTheme() {
    document.documentElement.setAttribute("data-theme", S.theme);
    const dark = S.theme === "dark";
    $("#theme-icon").querySelector("use").setAttribute("href", dark ? "#i-sun" : "#i-moon");
    $("#theme-label").textContent = dark ? "Switch to light theme" : "Switch to dark theme";
    $('[data-act="theme"]').setAttribute("aria-pressed", String(dark));
  }
  function setView(v) {
    S.view = v;
    writePrefs();
    render();
  }

  /* -------------------------------------------------------------- render */
  function render() {
    if (S.ended) return;
    if (!S.state) return;
    const active = document.activeElement;
    const fid = active && active.getAttribute ? active.getAttribute("data-fid") : null;
    // Dialogs and side panels handle their own focus (the activity log restores it itself).
    const inOverlay = !!(active && active.closest && active.closest("dialog, #chat, #activity"));

    $("#panel-skeleton").hidden = true;
    $("#view-app").classList.toggle("is-board", S.view === "board");
    $("#panel-board").hidden = S.view !== "board";
    $("#panel-list").hidden = S.view !== "list";
    $("#panel-inbox").hidden = S.view !== "inbox";
    $$(".segmented__btn").forEach(b => {
      const on = b.getAttribute("data-view") === S.view;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-pressed", String(on));
    });
    const inboxN = S.state.inbox.length;
    $("#inbox-count").hidden = inboxN === 0;
    $("#inbox-count").textContent = inboxN;
    $(".search__clear").hidden = !S.search;
    const n = activeFilterCount();
    $("#filter-count").hidden = n === 0;
    $("#filter-count").textContent = n;
    $("#filter-count").setAttribute("aria-label", n + " filters on");
    paintBadge();

    renderSummary();
    renderAttention();
    renderViewChip();
    renderMail();
    if (S.view === "board") renderBoard();
    else if (S.view === "list") renderList();
    else renderInbox();
    paintPopup(false);

    if (fid && !inOverlay) {
      const again = document.querySelector('[data-fid="' + CSS.escape(fid) + '"]');
      if (again) again.focus({ preventScroll: true });
    }
  }

  /* --------------------------------------------------------------- actions */
  function codeOf(el) {
    if (el.getAttribute("data-code")) return el.getAttribute("data-code");
    const holder = el.closest("[data-code]");
    return holder ? holder.getAttribute("data-code") : S.popup;
  }

  async function checkEmail() {
    await once("check", async () => {
      const d = await request("/api/check-email", { method: "POST", body: {} });
      if (!d) return;
      toast(d.message || "Checking your email.");
      loadState();
    });
  }

  async function bulkDecide(decision) {
    const codes = S.picked.slice();
    if (!codes.length) return;
    if (decision === "accept") await reviewCodes(codes, "accept", "");
    else openDecline(codes, $("#bulk-decline-label"));
  }

  async function ineligibleAdd(id) {
    await once("ineligible:" + id, async () => {
      const d = await request("/api/ineligible/" + enc(id) + "/add", { method: "POST", body: {} });
      if (!d) return;
      toast(d.line && d.ok === false ? d.line : (d.ok ? "Added to New." : "That job is already on your board."));
      loadState();
    });
  }

  function handleAct(act, el) {
    const code = codeOf(el);
    switch (act) {
      case "sign-out": signOut(); return;
      case "reload": location.reload(); return;
      case "retry": loadState(); return;
      case "theme": S.theme = S.theme === "dark" ? "light" : "dark"; applyTheme(); writePrefs(); return;
      case "open-filters": openFilters(el); return;
      case "clear-search": S.search = ""; $("#search").value = ""; render(); $("#search").focus(); return;
      case "clear-all":
        S.search = ""; $("#search").value = "";
        S.filters = { stages: [], fit: "all", where: "all", added: "all", showLow: S.filters.showLow };
        render(); return;
      case "check-email": checkEmail(); return;
      case "open-stats": openStats(el); return;
      case "open-activity": setActivityOpen(!S.activityOpen, el); return;
      case "close-activity": setActivityOpen(false, $('[data-act="open-activity"]')); return;
      case "open-views": openViews(el); return;
      case "view-use": applyView(el.getAttribute("data-name")); return;
      case "view-delete": once("view-delete", () => deleteView(el.getAttribute("data-name"))); return;
      case "view-save": openViewName(el); return;
      case "view-name-save": once("view-save", saveView); return;
      case "install-how": openDialog("dlg-install", el); return;
      case "install-dismiss": dismissInstall(); return;
      case "open-dup": openDup(code, el); return;
      case "dup-keep": keepBothDup(); return;
      case "dup-merge": once("dup-merge", mergeDup); return;
      case "dismiss-all":
        once("dismiss-all", async () => {
          for (const a of (S.state.attention || [])) await request("/api/attention/" + enc(a.id) + "/dismiss", { method: "POST", body: {} });
          loadState();
        });
        return;
      case "att-dismiss": attentionAction(el.getAttribute("data-id"), "dismiss"); return;
      case "att-snooze": attentionAction(el.getAttribute("data-id"), "snooze"); return;
      case "open-card": openCard(code, el); return;
      case "close-card": closeCard(); return;
      case "stage": openStageSheet(code, el); return;
      case "actions": openActionsSheet(code, el); return;
      case "pick-stage":
      case "move": {
        const to = el.getAttribute("data-stage");
        const dlg = el.closest("dialog");
        if (dlg && dlg.id !== "dlg-card") closeDialog(dlg);
        if (to) askToMove(code, to, el);
        return;
      }
      case "confirm-yes": confirmMove(); return;
      case "collapse": S.collapsed = !S.collapsed; writePrefs(); render(); return;
      case "note": openNote(code, el); return;
      case "save-note": saveNote(); return;
      case "accept": once("accept:" + code, () => reviewCodes([code], "accept", "")); return;
      case "decline": openDecline([code], el); return;
      case "decline-confirm": confirmDecline(); return;
      case "pick-inbox": {
        const i = S.picked.indexOf(code);
        if (i > -1) S.picked.splice(i, 1); else S.picked.push(code);
        render(); return;
      }
      case "inbox-select-all": {
        const all = S.state.inbox.map(x => x.code);
        S.picked = S.picked.length === all.length ? [] : all;
        render(); return;
      }
      case "bulk-accept": bulkDecide("accept"); return;
      case "bulk-decline": bulkDecide("decline"); return;
      case "bulk-clear": S.picked = []; render(); return;
      case "ineligible-add": ineligibleAdd(el.getAttribute("data-id")); return;
      case "undo-activity": {
        const row = (S.activity || [])[Number(el.getAttribute("data-idx"))];
        if (row && row.undo) once("undo-activity", async () => {
          await undoWith(row.undo);
          await loadActivity();
          const first = $("#activity-log button");
          if (first && S.activityOpen) first.focus();
        });
        return;
      }
      case "apply-filters":
        if (S.draft) { S.draft.showLow = $("#f-lowfit").checked; S.filters = S.draft; }
        S.draft = null;
        closeDialog("dlg-filters");
        render(); return;
      case "reset-filters":
        S.draft = { stages: [], fit: "all", where: "all", added: "all", showLow: false };
        renderFilterSheet(); return;
      case "open-add": openAdd(el); return;
      case "add-start": startAdd(); return;
      case "add-stop": {
        const job = S.add.job;
        if (job) request("/api/add-links/" + enc(job.id) + "/cancel", { method: "POST", body: {} });
        return;
      }
      case "add-reset": S.add = { mode: "input", job: null, text: "", anyway: false, picks: [] }; renderAdd(); return;
      case "add-anyway": {
        const jobId = el.getAttribute("data-job");
        const n = Number(el.getAttribute("data-n"));
        once("anyway:" + jobId + n, () => addAction("/api/add-links/" + enc(jobId) + "/anyway", { n: n }));
        return;
      }
      case "pick-confirm": {
        readPicks();
        const jobId = el.getAttribute("data-job");
        const selected = S.add.picks.slice();
        if (!selected.length) { toast("Pick at least one job first."); return; }
        once("confirm:" + jobId, () => addAction("/api/add-links/" + enc(jobId) + "/confirm", { selected: selected, anyway: !!S.add.anyway }));
        return;
      }
      case "open-chat": S.chatTrigger = el; setChatOpen(true); return;
      case "close-chat": setChatOpen(false, chatReturn()); return;
      case "chat-send": sendChat($("#chat-input").value); return;
      case "chat-suggest": sendChat(el.getAttribute("data-q")); return;
      case "chat-stop": stopChat(el.getAttribute("data-id")); return;
      case "prepare": prepareApplication(code); return;
      case "open-messages": goMessages(""); return;
      case "msgs-close": leaveMessages(); return;
      case "msgs-list": goMessages(""); return;
      case "open-thread": closeDialog("dlg-card"); goMessages(code); return;
      case "msgs-unread-only": S.msgs.unreadOnly = !S.msgs.unreadOnly; paintList(); return;
      case "msgs-pane": {
        S.msgs.screen = el.getAttribute("data-pane") === "preview" ? "preview" : "chat";
        $("#msgs").setAttribute("data-screen", S.msgs.screen);
        const target = S.msgs.screen === "chat" ? $("#msg-input") : $('#msgs-preview [data-act="msgs-pane"], #msgs-preview .segmented__btn');
        if (target && !window.matchMedia("(pointer: coarse)").matches) target.focus();
        return;
      }
      case "msgs-doc": {
        const M = S.msgs;
        M.doc = el.getAttribute("data-doc") === "letter" ? "letter" : "resume";
        if (!window.matchMedia("(min-width: 1180px)").matches) { M.screen = "preview"; $("#msgs").setAttribute("data-screen", "preview"); }
        paintPreview();
        const again = $('#msgs-preview .segmented__btn.is-on');
        if (again) again.focus();
        return;
      }
      case "copy-path": copyText(el.getAttribute("data-path") || ""); return;
      case "resume-approve": once("resume:" + code, () => resumeCall(code, { approve: true })); return;
      case "resume-undo": once("resume:" + code, () => resumeCall(code, { undo: true })); return;
      case "resume-retry": once("resume:" + code, () => resumeCall(code, {})); return;
      case "resume-stop": if (S.msgs.code) stopRun(S.msgs.code); return;
      default: return;
    }
  }

  /* -------------------------------------------------------------- events */
  document.addEventListener("click", function (ev) {
    const t = ev.target;
    if (!t || !t.closest) return;
    const hit = t.closest("[data-act], [data-view], [data-f], [data-sortby], [role='tab']");
    if (hit && hit.hasAttribute("data-act")) { handleAct(hit.getAttribute("data-act"), hit); return; }
    if (hit && hit.hasAttribute("data-view")) { setView(hit.getAttribute("data-view")); return; }
    if (hit && hit.getAttribute("role") === "tab") { S.stage = hit.getAttribute("data-stage"); render(); return; }
    if (hit && hit.hasAttribute("data-f")) {
      const group = hit.getAttribute("data-f");
      const value = hit.getAttribute("data-value");
      if (!S.draft) S.draft = JSON.parse(JSON.stringify(S.filters));
      if (group === "stage") {
        const at = S.draft.stages.indexOf(value);
        if (at > -1) S.draft.stages.splice(at, 1); else S.draft.stages.push(value);
      } else {
        S.draft[group] = value;
      }
      renderFilterSheet();
      const again = document.querySelector('[data-f="' + group + '"][data-value="' + value + '"]');
      if (again) again.focus();
      return;
    }
    if (hit && hit.hasAttribute("data-sortby")) {
      const key = hit.getAttribute("data-sortby");
      if (S.sort.key === key) S.sort.dir = S.sort.dir === "asc" ? "desc" : "asc";
      else S.sort = { key: key, dir: key === "fit" || key === "added" || key === "posted" ? "desc" : "asc" };
      render();
      const again = document.querySelector('[data-sortby="' + key + '"]');
      if (again) again.focus();
      return;
    }
    const card = t.closest(".card[data-code], .rowcard[data-code], .inbox-item[data-code], tr[data-code]");
    if (card && !t.closest("button, a, input, label, select, textarea")) {
      openCard(card.getAttribute("data-code"), card);
    }
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Enter" || !ev.target || !ev.target.matches) return;
    if (ev.target.matches(".card[data-code], .rowcard[data-code]")) {
      ev.preventDefault();
      openCard(ev.target.getAttribute("data-code"), ev.target);
    }
  });

  $("#search").addEventListener("input", function () { S.search = this.value; render(); });
  $("#search").addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && this.value) { this.value = ""; S.search = ""; render(); }
  });
  $("#sort-select").addEventListener("change", function () {
    const parts = this.value.split("-");
    S.sort = { key: parts[0], dir: parts[1] };
    render();
  });
  $("#f-lowfit").addEventListener("change", function () {
    if (!S.draft) S.draft = JSON.parse(JSON.stringify(S.filters));
    S.draft.showLow = this.checked;
  });
  document.addEventListener("change", function (ev) {
    const t = ev.target;
    if (t.id === "pick-anyway") {
      readPicks();
      S.add.anyway = t.checked;
      renderAdd();
    } else if (t.hasAttribute && t.hasAttribute("data-pick")) {
      readPicks();
    } else if (t.name === "dup-keep" && t.checked) {
      pickDupKeep(t.value);
    }
  });
  $("#view-name-input").addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { ev.preventDefault(); once("view-save", saveView); }
  });
  document.addEventListener("input", function (ev) {
    const t = ev.target;
    if (t.id === "add-text") {
      S.add.text = t.value;
      const btn = $('[data-act="add-start"]');
      if (btn) btn.disabled = !t.value.trim();
    }
  });
  $("#stagebar").addEventListener("keydown", function (ev) {
    if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
    const tabs = $$(".stage-tab", this);
    const at = tabs.indexOf(document.activeElement);
    if (at < 0) return;
    const next = tabs[(at + (ev.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    next.focus();
    S.stage = next.getAttribute("data-stage");
    ev.preventDefault();
    render();
  });
  $("#chat-input").addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { ev.preventDefault(); sendChat(this.value); }
  });
  document.addEventListener("keydown", function (ev) {
    // Focus can be on the page after sending (the send button disables), so Escape still closes the chat.
    if (ev.key !== "Escape" || document.querySelector("dialog[open]")) return;
    // Escape inside a text field (e.g. clearing the search) belongs to the field, not the drawer.
    const t = ev.target;
    if (t && t.closest && t.closest("input, textarea, select")) return;
    if (S.activityOpen) {
      if (!$("#activity").contains(document.activeElement)) setActivityOpen(false, $('[data-act="open-activity"]'));
      return;
    }
    if ($("#chat").hidden || $("#chat").contains(document.activeElement)) return;
    setChatOpen(false, chatReturn());
  });
  $("#activity").addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && !document.querySelector("dialog[open]")) {
      ev.preventDefault();
      setActivityOpen(false, $('[data-act="open-activity"]'));
    }
  });
  // Below 1024px the activity panel covers the whole screen (app.css: .chat is inset:0 until the 1024px rule), so keep Tab inside it there.
  $("#activity").addEventListener("keydown", function (ev) {
    if (ev.key !== "Tab" || window.matchMedia("(min-width: 1024px)").matches) return;
    const items = $$("button:not([disabled]), a[href]", this).filter(el => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  });
  $("#login-form").addEventListener("submit", signIn);
  $("#chat").addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") { ev.preventDefault(); setChatOpen(false, chatReturn()); return; }
    if (ev.key !== "Tab") return;
    const items = $$("button:not([disabled]), input, textarea", this).filter(el => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  });

  window.addEventListener("hashchange", route);
  $("#msgs-search").addEventListener("input", function () { S.msgs.query = this.value; paintList(); });
  document.addEventListener("submit", function (ev) {
    if (ev.target && ev.target.id === "msg-compose") { ev.preventDefault(); sendMsg(); }
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && ev.target && ev.target.id === "msg-input") { ev.preventDefault(); sendMsg(); return; }
    if (ev.key !== "Escape" || !S.msgs.open || !$("#chat").hidden || document.querySelector("dialog[open]")) return;
    const t = ev.target;
    if (t && t.closest && t.closest("input, textarea, select")) return;
    leaveMessages();
  });

  window.addEventListener("offline", () => setBanner("offline", true));
  window.addEventListener("online", () => { setBanner("offline", false); loadState(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadState(); });
  setInterval(() => { if (!document.hidden) loadState(); }, 60000);

  /* ---------------------------------------------------------------- start */
  function init() {
    const prefs = readPrefs();
    if (prefs.theme === "dark" || prefs.theme === "light") S.theme = prefs.theme;
    if (typeof prefs.collapsed === "boolean") S.collapsed = prefs.collapsed;
    if (prefs.installDismissed === true) S.installDismissed = true;
    if (Array.isArray(prefs.dupKept)) S.dupKept = prefs.dupKept.filter(k => typeof k === "string").slice(-100);
    if (prefs.view === "list" || prefs.view === "board" || prefs.view === "inbox") S.view = prefs.view;
    else if (window.matchMedia("(max-width: 700px)").matches) S.view = "list";
    applyTheme();
    $("#view-login").hidden = true;
    $("#view-app").hidden = false;
    $("#panel-skeleton").hidden = false;
    $("#panel-board").hidden = true;
    $("#panel-list").hidden = true;
    $("#panel-inbox").hidden = true;
    if (!navigator.onLine) setBanner("offline", true);
    renderInstall();
    loadState();
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => { /* offline copy is optional */ });
    }
  }
  init();
})();
