/* Demo-only stand-in for the board's server. build.py injects this file (and demo/fixtures.json) before app.js.
 * It answers every /api/* request from a made-up snapshot, shifted so "today" is your today. Changes you make
 * (moving a card, notes, saved views) live in memory only and vanish on reload. Nothing leaves your browser.
 * The assistant features (adding links, chat, resume writer, email check) are not connected in the demo. */
(function () {
  "use strict";
  var SNAP = JSON.parse(document.getElementById("demo-fixtures").textContent);
  var DAY = 86400;
  var LABEL = { "new": "New", applied: "Applied", interview: "Interview", offer: "Offer", rejected: "Rejected", parked: "Not for me" };

  // ---- shift the snapshot's dates so the newest data looks fresh
  function isoToDays(s) { var p = s.split("-"); return Math.round(Date.UTC(+p[0], +p[1] - 1, +p[2]) / (DAY * 1000)); }
  function daysToIso(n) { return new Date(n * DAY * 1000).toISOString().slice(0, 10); }
  function localToday() { var d = new Date(); return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2); }
  var DELTA = isoToDays(localToday()) - isoToDays(SNAP.today);
  var NOW = Math.floor(Date.now() / 1000);
  var EPOCH_SHIFT = NOW - SNAP.now;
  function monday(iso) { var n = isoToDays(iso); var wd = (n + 3) % 7; return daysToIso(n - wd); }   // 1970-01-01 was a Thursday
  function shift(v, key) {
    if (typeof v === "string") return v.replace(/\b(\d{4}-\d{2}-\d{2})\b/g, function (m) { return daysToIso(isoToDays(m) + DELTA); });
    if (typeof v === "number") return (v > 1e9 && v < 4e9) ? v + EPOCH_SHIFT : v;
    if (Array.isArray(v)) return v.map(function (x) { return shift(x, key); });
    if (v && typeof v === "object") {
      var o = {};
      Object.keys(v).forEach(function (k) {
        o[k] = k === "weekOf" ? monday(shift(v[k])) : shift(v[k], k);
      });
      return o;
    }
    return v;
  }
  var DB = shift(SNAP);
  var STATE = DB.state;
  var ACTIVITY = DB.activity;
  var undoLog = [];

  // ---- helpers
  function clone(x) { return JSON.parse(JSON.stringify(x)); }
  function json(status, body) {
    return new Response(JSON.stringify(body), { status: status, headers: { "Content-Type": "application/json" } });
  }
  function fail(status, msg) { return json(status, { ok: false, error: msg }); }
  function findCard(code) {
    var c = (code || "").toUpperCase();
    return STATE.cards.find(function (x) { return x.code.toUpperCase() === c; }) ||
           STATE.inbox.find(function (x) { return x.code.toUpperCase() === c; });
  }
  function today() { return localToday(); }
  function log(card, kind, text, undo) {
    ACTIVITY.unshift({ ts: Math.floor(Date.now() / 1000), code: card.code, title: card.title, company: card.company, by: "you", kind: kind, text: text, undo: undo || null });
    STATE.now = Math.floor(Date.now() / 1000);
  }
  function setStage(card, stage) {
    var prev = card.stage;
    card.stage = stage;
    if (stage !== "new" && stage !== "parked" && stage !== "rejected" && !card.appliedOn) card.appliedOn = today();
    if (stage === "parked") card.reason = "Set aside";
    card.timeline.push({ date: today(), label: "Moved to " + LABEL[stage], by: "you" });
    log(card, "stage", "Moved to " + LABEL[stage], { code: card.code, to: prev });
    recount();
    return prev;
  }
  function recount() {
    var applied = STATE.cards.filter(function (c) { return c.appliedOn; }).length;
    STATE.stats.applied = applied;
  }
  function unsupported() {
    return fail(400, "This is a demo with sample data. The assistant (link reading, chat, resume writer, email check) isn't connected.");
  }

  // ---- routes
  function route(method, path, query, body) {
    var p = path.replace(/^\/api\//, "").split("/").map(decodeURIComponent);
    var n = p.length;
    if (method === "GET") {
      if (p[0] === "session") return json(200, { authed: true });
      if (p[0] === "state") return json(200, clone(STATE));
      if (p[0] === "activity") return json(200, { items: clone(ACTIVITY).slice(0, parseInt(query.get("limit") || "60", 10)) });
      if (p[0] === "messages") return json(200, clone(DB.messages));
      if (p[0] === "chat" && p[1] === "history") return json(200, { items: [] });
      if (n === 3 && p[0] === "cards" && p[2] === "resume") return json(200, { resume: null });
      if (n === 4 && p[0] === "cards" && p[2] === "resume" && p[3] === "chat") return json(200, { messages: [], resume: null, readAt: "" });
      return unsupported();
    }
    if (p[0] === "login" || p[0] === "logout") return json(200, { ok: true });
    if (n === 3 && p[0] === "cards" && p[2] === "stage") {
      var c = findCard(p[1]);
      if (!c) return fail(404, "I can't find that card.");
      if (!LABEL[body.stage]) return fail(400, "That stage doesn't exist.");
      var prev = setStage(c, body.stage);
      return json(200, { ok: true, message: "Moved to " + LABEL[body.stage] + ".", undo: { code: c.code, to: prev } });
    }
    if (n === 3 && p[0] === "cards" && p[2] === "note") {
      var cn = findCard(p[1]);
      var text = String(body.text || "").trim();
      if (!text) return fail(400, "Write something first.");
      if (!cn) return fail(404, "I can't find that card.");
      cn.notes.push({ date: today(), text: text });
      log(cn, "note", "Note: " + text);
      return json(200, { ok: true, message: "Note saved." });
    }
    if (p[0] === "undo") {
      var cu = findCard(body.code);
      if (!cu) return fail(404, "I can't find that card.");
      if (body.review) {
        STATE.cards = STATE.cards.filter(function (x) { return x !== cu; });
        STATE.inbox.unshift(Object.assign(cu, { stage: "new", eligible: true, eligibility: "Passes your checks", found: "Found by your assistant" }));
        return json(200, { ok: true, message: "Back in the Inbox." });
      }
      if (!LABEL[body.to]) return fail(400, "Nothing to undo.");
      cu.stage = body.to;
      cu.timeline.push({ date: today(), label: "Moved to " + LABEL[body.to] + " (undo)", by: "you" });
      recount();
      return json(200, { ok: true, message: "Moved back to " + LABEL[body.to] + "." });
    }
    if (n === 3 && p[0] === "inbox" && (p[2] === "accept" || p[2] === "decline")) {
      var ci = STATE.inbox.find(function (x) { return x.code.toUpperCase() === p[1].toUpperCase(); });
      if (!ci) return fail(404, "That one is already decided.");
      STATE.inbox = STATE.inbox.filter(function (x) { return x !== ci; });
      ci.stage = p[2] === "accept" ? "new" : "parked";
      if (p[2] === "decline") ci.reason = (body && body.reason) || "Declined from the Inbox";
      ci.timeline.push({ date: today(), label: p[2] === "accept" ? "Added to your board" : "Declined", by: "you" });
      STATE.cards.push(ci);
      log(ci, "review", "Inbox: " + p[2] + "ed");
      return json(200, { ok: true, message: p[2] === "accept" ? "Added to your board." : "Declined.", undo: { code: ci.code, review: "pending" } });
    }
    if (p[0] === "merge") {
      var drop = findCard(body.drop);
      if (!drop || !findCard(body.keep)) return fail(404, "I can't find that card.");
      STATE.cards = STATE.cards.filter(function (x) { return x !== drop; });
      return json(200, { ok: true, message: "Merged into [" + body.keep + "]." });
    }
    if (n === 3 && p[0] === "attention") {
      STATE.attention = STATE.attention.filter(function (a) { return a.id !== p[1]; });
      return json(200, { ok: true });
    }
    if (p[0] === "views" && n === 1) {
      var name = String(body.name || "").trim();
      if (!name) return fail(400, "Give the view a name.");
      STATE.views = STATE.views.filter(function (v) { return v.name !== name; }).concat([{ name: name, filters: body.filters || {} }]);
      return json(200, { ok: true, views: STATE.views });
    }
    if (p[0] === "views" && n === 2 && method === "DELETE") {
      STATE.views = STATE.views.filter(function (v) { return v.name !== p[1]; });
      return json(200, { ok: true, views: STATE.views });
    }
    if (p[0] === "check-email") return json(200, { ok: true, message: "Demo only: no mailbox is connected." });
    return unsupported();
  }

  var realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var u;
    try { u = new URL(url, location.href); } catch (e) { return realFetch(input, init); }
    var m = u.pathname.match(/\/api\/.*$/);
    if (!m || u.origin !== location.origin) return realFetch(input, init);
    var method = ((init && init.method) || "GET").toUpperCase();
    var body = {};
    try { body = init && init.body ? JSON.parse(init.body) : {}; } catch (e) { body = {}; }
    return new Promise(function (resolve) {
      setTimeout(function () {
        try { resolve(route(method, m[0], u.searchParams, body)); }
        catch (e) { resolve(fail(400, "Demo: that request could not be handled.")); }
      }, 120);   // a little latency so spinners show
    });
  };
  // Download links (CSV, calendar) are plain <a href="/api/..."> in the page: build the file in the browser instead.
  function download(name, type, text) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: type }));
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }
  function csvCell(v) { v = String(v == null ? "" : v); if (/^[=+\-@\t\r]/.test(v)) v = "'" + v; return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
  document.addEventListener("click", function (ev) {
    var a = ev.target.closest && ev.target.closest('a[href^="/api/"]');
    if (!a) return;
    var href = a.getAttribute("href");
    if (/export\.csv$/.test(href)) {
      ev.preventDefault();
      var rows = [["Code", "Title", "Company", "Stage", "Fit", "Location", "Added", "Applied"]].concat(STATE.cards.map(function (c) {
        return [c.code, c.title, c.company, LABEL[c.stage], c.fit, c.location, c.added, c.appliedOn];
      }));
      download("applications.csv", "text/csv", rows.map(function (r) { return r.map(csvCell).join(","); }).join("\r\n") + "\r\n");
    } else if (/\/calendar\.ics$|\/deadlines\.ics$|\/api\/[^/]*\.ics$/.test(href)) {
      ev.preventDefault();
      var one = (href.match(/\/api\/cards\/([^/]+)\/calendar\.ics$/) || [])[1];
      var ev2 = STATE.attention.filter(function (x) { return x.due && (!one || x.code === decodeURIComponent(one)); }).map(function (x) {
        return ["BEGIN:VEVENT", "UID:" + x.id + "@demo", "DTSTAMP:" + today().replace(/-/g, "") + "T000000Z",
                "DTSTART;VALUE=DATE:" + x.due.replace(/-/g, ""), "SUMMARY:" + String(x.title).replace(/([,;\\])/g, "\\$1").replace(/\r?\n/g, " "), "END:VEVENT"].join("\r\n");
      });
      download("deadlines.ics", "text/calendar", ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//jobs-agent demo//EN"].concat(ev2, ["END:VCALENDAR"]).join("\r\n") + "\r\n");
    }
  }, true);

  window.__DEMO__ = true;
})();
