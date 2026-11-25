#!/usr/bin/env python3
"""Shared helpers for the jobs_* scripts (board reads, card parsing, codes, URL keys, dates).

Reads the kanban board read-only through sqlite. All writes go through the `hermes kanban` CLI
(see `kanban()`), never through sqlite, so Hermes keeps its events and invariants.
"""
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import jobs_config as CFG

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
RESPONSE_RE = re.compile(r"(?is)^\s*RESPONSE\s+(\d{4}-\d{2}-\d{2})\s+([a-z-]+)\s*:?\s*(.*)$")
APPLIED_COMMENT_RE = re.compile(r"(?is)^\s*applied\b[\s:-]*(\d{4}-\d{2}-\d{2})?")
TAG_RE = re.compile(r"^\s*\[([^\]]{1,12})\]\s*")
RESPONSE_TYPES = ("interview", "assessment", "rejection", "offer", "info-request")
STAGE_BY_RESPONSE = {"rejection": "Rejected", "offer": "Offer", "interview": "Interview",
                     "assessment": "Assessment", "info-request": "Applied"}
STAGES = ("New", "Applied", "Assessment", "Interview", "Offer", "Rejected", "Parked")
# Stage comments written by the board / jobs_mark: `STAGE 2026-11-24 interview from applied: note`.
# The author of the comment says who moved the card: "you" (the user, on the board) or anything else (the assistant).
STAGE_RE = re.compile(r"(?is)^\s*STAGE\s+(\d{4}-\d{2}-\d{2})\s+(new|applied|assessment|interview|offer|rejected|parked)"
                      r"(?:\s+from\s+(new|applied|assessment|interview|offer|rejected|parked))?\s*:?\s*(.*)$")
REVIEW_RE = re.compile(r"(?is)^\s*REVIEW\s+(pending|accepted|declined)\b\s*:?\s*(.*)$")
NOTE_RE = re.compile(r"(?is)^\s*NOTE\s+(\d{4}-\d{2}-\d{2})\s*:?\s*(.*)$")
DEADLINE_RE = re.compile(r"(?i)\[deadline\s+(\d{4}-\d{2}-\d{2})\]")
USER_AUTHORS = ("you",)
TRACKING_PARAMS = {"ref", "trk", "trackingid", "refid", "src", "source", "gclid", "fbclid", "mc_cid", "mc_eid", "gh_src", "lever-source", "lever-origin"}
KEEP_PARAMS = {"gh_jid", "jobid", "job_id", "jid", "req_id", "requisitionid", "id", "jk", "reqid"}


# ---------- paths / environment ----------
def board_db():
    return Path(os.environ.get("JOBS_BOARD_DB") or Path.home() / ".hermes" / "kanban" / "boards" / "jobs" / "kanban.db")


def state_dir():
    return Path(os.environ.get("JOBS_STATE_DIR") or Path.home() / ".hermes" / "state")


def export_dir():
    return CFG.export_dir()

_PATH_RE = re.compile(r"(?<![\w.:/])~?/(?:[\w.\-@+~]+/)+[\w.\-@+~]*")


def scrub_paths(text):
    """Remove local file paths from text that goes to the browser or the chat. Web links are left alone."""
    out = str(text or "")
    roots = {str(Path.home()), str(state_dir()), str(export_dir()), str(Path(__file__).resolve().parent)}
    for root in sorted((r for r in roots if len(r) > 3), key=len, reverse=True):   # known folders first: they may contain spaces
        out = re.sub(re.escape(root) + r"(?:/[^:;,'\"\n]{0,160}?\.\w{2,5}\b|/[^\s:;,'\"]*)?", "a file", out)
    out = re.sub(r"file://\S*", "a file", out)
    return _PATH_RE.sub("a file", out)


def hermes_bin():
    exe = os.environ.get("HERMES_BIN") or shutil.which("hermes")
    return exe or str(Path.home() / ".local" / "bin" / "hermes")


def tz():
    name = CFG.timezone_name()
    try:
        return ZoneInfo(name) if ZoneInfo else timezone.utc
    except Exception:
        return timezone.utc


def now_ts():
    return int(datetime.now(timezone.utc).timestamp())


def today():
    return datetime.now(tz()).strftime("%Y-%m-%d")


def fmt_date(ts):
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz()).strftime("%Y-%m-%d")


def clean(v, limit=500):
    return CTRL_RE.sub(" ", str(v or "")).replace("\r", "").strip()[:limit]


def one_line(v, limit=500):
    return re.sub(r"\s+", " ", clean(v, limit * 2)).strip()[:limit]


# ---------- kanban CLI (writes) ----------
def kanban(*args, input_text=None):
    p = subprocess.run([hermes_bin(), "kanban", "--board", "jobs", *args], input=input_text,
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"hermes kanban {args[0]} failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


# ---------- URLs / keys ----------
def norm_url(url):
    """Normalized job URL used for dedupe keys (same rules the skill always described)."""
    url = (url or "").strip()
    if not re.match(r"(?i)^https?://", url):
        return ""
    try:
        u = urlsplit(url)
    except ValueError:
        return ""
    host = (u.hostname or "").lower()
    if host.startswith("www.") and not host.endswith("linkedin.com"):
        host = host[4:]
    path = u.path or ""
    if host.endswith("linkedin.com"):
        m = re.search(r"/jobs/view/(?:[^/?]*?-)?(\d{6,})", path)
        if m:
            return f"https://www.linkedin.com/jobs/view/{m.group(1)}"
    q = parse_qsl(u.query, keep_blank_values=False)
    if host.endswith("indeed.com"):
        for k, v in q:
            if k.lower() == "jk" and v:
                return f"indeed:{v}"
    kept = []
    for k, v in q:
        kl = k.lower()
        if kl in KEEP_PARAMS:
            kept.append((k, v))
        elif kl.startswith("utm_") or kl in TRACKING_PARAMS:
            continue
        else:
            kept.append((k, v))
    kept.sort(key=lambda kv: kv[0])
    path = path.rstrip("/")
    try:
        port = f":{u.port}" if u.port and u.port not in (80, 443) else ""
    except ValueError:
        port = ""
    return urlunsplit(("https", host + port, path, urlencode(kept), ""))


def sha_key(normalized):
    return "job-" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def url_key(url):
    n = norm_url(url)
    return sha_key(n) if n else ""


def fallback_key(company, title, location=None):
    parts = [(company or "").strip().lower(), (title or "").strip().lower()]
    if location is not None:
        parts.append(location.strip().lower())
    return sha_key("|".join(parts))


def applied_key(company, title):
    """Applied-card key from the skill: lowercase(company)|lowercase(title)."""
    return sha_key(f"{(company or '').strip().lower()}|{(title or '').strip().lower()}")


REQ_PREFIX_RE = re.compile(r"(?i)(?<![a-z0-9])((?:r|jr|req|reqid|job|id)[-_]?\d{4,})(?![a-z0-9])")
REQ_NUM_RE = re.compile(r"(?<![0-9a-zA-Z])(\d{6,})(?![0-9a-zA-Z])")
REQ_VALUE_RE = re.compile(r"^[A-Za-z]{0,3}[-_]?\d{4,}$")


def req_ids(url):
    """Requisition-like ids found in a job URL, as `host|id` tokens (same host only ever compares equal)."""
    n = norm_url(url)
    if not n or n.startswith("indeed:"):
        return set()
    u = urlsplit(n)
    host = (u.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    ids = set()
    for m in REQ_PREFIX_RE.finditer(u.path):
        ids.add(m.group(1))
    for m in REQ_NUM_RE.finditer(u.path):
        v = m.group(1)
        if not re.match(r"20\d{6}$", v):          # a YYYYMMDD slug is a date, not a requisition id
            ids.add(v)
    for k, v in parse_qsl(u.query):
        if k.lower() in KEEP_PARAMS and REQ_VALUE_RE.match(v):
            ids.add(v)
    return {f"{host}|{re.sub(r'[-_]', '', i.lower())}" for i in ids}


def link_has_id(url):
    """True when the URL identifies one specific job (id in it), not a shared listing page."""
    n = norm_url(url)
    return bool(n) and (n.startswith("indeed:") or bool(req_ids(url)))


def loc_norm(loc):
    t = re.sub(r"(?i)british columbia", "bc", loc or "")
    t = re.sub(r"(?i)\b(not stated|unknown|n/a)\b", "", t)
    return alnum(t)


def alnum(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_SUFFIX_RE = re.compile(r"(?i)\b(inc|ltd|llc|corp|corporation|co|company|limited|canada|ulc|lp)\b\.?")


def company_norm(c):
    return alnum(_SUFFIX_RE.sub(" ", c or ""))


# ---------- card parsing ----------
def parse_body(body):
    f = {}
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("company:"):
            for seg in line.split(" | "):
                k, _, v = seg.partition(":")
                if v:
                    f[k.strip().lower()] = v.strip()
            continue
        m = re.match(r"^([A-Za-z][A-Za-z /]{1,40}?):\s*(.*)$", line)
        if m:
            key = m.group(1).strip().lower()
            if key not in f:
                f[key] = m.group(2).strip()
    return f


def first_url(text):
    m = re.search(r"https?://[^\s<>\"')\]]+", text or "")
    return m.group(0) if m else ""


class Card(dict):
    """dict with attribute access; built by load_cards()."""
    __getattr__ = dict.get


APPLIED_STAGES = {"Applied", "Assessment", "Interview", "Offer"}


def build_card(row, comments):
    tid, title, body, status, created, completed, ikey = row
    f = parse_body(body)
    tag = TAG_RE.match(title or "")
    tag_s = tag.group(1) if tag else ""
    fit = None
    m = re.match(r"\s*(\d{1,3})", f.get("fit", ""))
    if m:
        fit = int(m.group(1))
    elif tag_s.isdigit():
        fit = int(tag_s)
    base = TAG_RE.sub("", title or "", count=1).strip()
    company = f.get("company", "")
    if not company and " - " in base:
        company = base.rsplit(" - ", 1)[1].strip()
    bare = base
    if " - " in base:
        head, tail = base.rsplit(" - ", 1)
        t_n, c_n = alnum(tail), alnum(company)
        if t_n and (not c_n or c_n.startswith(t_n) or t_n.startswith(c_n)):
            bare = head.strip()
            if " - " in bare and alnum(bare.rsplit(" - ", 1)[1]) and alnum(bare.rsplit(" - ", 1)[1]) in c_n:
                bare = bare.rsplit(" - ", 1)[0].strip()
    link = f.get("link") or first_url(body)
    if link.lower() == "none":
        link = ""
    responses, applied_note_date = [], ""
    stage_events, notes, review, review_note, activity = [], [], "accepted", "", []
    for seq, (author, text, ts) in enumerate(comments):
        by_you = (author or "").lower() in USER_AUTHORS
        rm = RESPONSE_RE.match(text or "")
        sm = STAGE_RE.match(text or "")
        vm = REVIEW_RE.match(text or "")
        nm = NOTE_RE.match(text or "")
        if rm:
            note = rm.group(3)
            dl = DEADLINE_RE.search(note)
            responses.append({"date": rm.group(1), "type": rm.group(2).lower(), "text": one_line(DEADLINE_RE.sub("", note), 300),
                              "deadline": dl.group(1) if dl else "", "ts": ts, "seq": seq, "by_you": by_you})
            activity.append({"ts": ts, "seq": seq, "kind": "response", "by_you": by_you, "text": f"{rm.group(2).lower()}: {one_line(note, 160)}"})
        elif sm:
            stage_events.append({"ts": ts, "seq": seq, "date": sm.group(1), "stage": sm.group(2).lower(), "from": (sm.group(3) or "").lower(),
                                 "text": one_line(sm.group(4), 250), "by_you": by_you})
            activity.append({"ts": ts, "seq": seq, "kind": "stage", "by_you": by_you, "stage": sm.group(2).lower(),
                             "from": (sm.group(3) or "").lower(), "text": one_line(sm.group(4), 160)})
        elif vm:
            review, review_note = vm.group(1).lower(), one_line(vm.group(2), 250)
            activity.append({"ts": ts, "seq": seq, "kind": "review", "by_you": by_you, "text": f"{review} {review_note}".strip()})
        elif nm:
            notes.append({"date": nm.group(1), "text": clean(nm.group(2), 1500), "ts": ts, "by_you": by_you})
            activity.append({"ts": ts, "seq": seq, "kind": "note", "by_you": by_you, "text": one_line(nm.group(2), 160)})
        elif APPLIED_COMMENT_RE.match(text or ""):
            if not applied_note_date:
                am = APPLIED_COMMENT_RE.match(text)
                applied_note_date = am.group(1) or fmt_date(ts)
            activity.append({"ts": ts, "seq": seq, "kind": "applied", "by_you": by_you, "text": one_line(text, 200)})
    responses.sort(key=lambda r: (r["date"], r["ts"]))
    applied = tag_s.upper() == "APPLIED" or status == "done"
    last = responses[-1] if responses else None
    real = [r for r in responses if r["type"] != "info-request"]
    decisive = real[-1] if real else None       # an info-request never moves a card backwards
    stage = "New"
    if applied:
        stage = STAGE_BY_RESPONSE.get(decisive["type"], "Applied") if decisive else "Applied"
    elif decisive:
        stage = STAGE_BY_RESPONSE.get(decisive["type"], "New")
    last_stage = stage_events[-1] if stage_events else None
    if last_stage is not None and not (decisive and decisive["seq"] > last_stage["seq"]):
        # a later explicit STAGE comment wins over what the application tag / responses imply,
        # unless a decisive response arrived after it
        stage = last_stage["stage"].capitalize()
        # the run of stage events since the last reset to New/Parked (an undo is a reset)
        run = []
        for ev in reversed(stage_events):
            if ev["stage"].capitalize() in ("New", "Parked"):
                break
            run.append(ev)
        run.reverse()
        reset_before = len(run) < len(stage_events)
        run_applied = [ev for ev in run if ev["stage"].capitalize() in APPLIED_STAGES]
        if stage in APPLIED_STAGES:
            applied = True
        elif stage == "Rejected":
            # a rejection only counts as applied when there is evidence of an application
            applied = bool(run_applied) or (applied and not reset_before)
        else:
            applied = False
        if applied and run_applied and (reset_before or not applied_note_date):
            applied_note_date = run_applied[0]["date"]
    src = f.get("source", "")
    rec = re.search(r"\d{4}-\d{2}-\d{2}", src)
    return Card(
        id=tid, code=tid[-4:].upper(), code6=tid[-6:].upper(), status=status, tag=tag_s, title=bare, company=company,
        full_title=title, fit=fit, location=f.get("location", ""), pay=f.get("pay", ""),
        schedule=f.get("schedule", ""), source=src.split(",")[0].strip().lower(),
        received=rec.group(0) if rec else "",
        posted=f.get("posted", "unknown") or "unknown", link=link, summary=f.get("summary", ""),
        reason=re.sub(r"^\s*\d{1,3}\s*/\s*100\s*-?\s*", "", f.get("fit", "")), research=f.get("research", ""),
        action=f.get("action requested of user / deadline", ""), description=f.get("description", ""),
        created_at=created, completed_at=completed, added=fmt_date(created), applied=applied,
        applied_date=(applied_note_date or fmt_date(completed) or fmt_date(created)) if applied else "",
        stage=stage, responses=responses, last_response=last, idempotency_key=ikey or "", body=body or "",
        stage_events=stage_events, notes=notes, review=review, review_note=review_note, activity=activity,
        requirements=f.get("requirements", ""), gate=f.get("gate", ""),
    )


def load_cards(include_archived=False):
    p = board_db()
    if not p.exists():
        raise RuntimeError(f"kanban board database not found: {p}")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
    try:
        q = "select id,title,body,status,created_at,completed_at,idempotency_key from tasks"
        if not include_archived:
            q += " where status != 'archived'"
        rows = con.execute(q + " order by created_at").fetchall()
        com = {}
        for tid, author, text, ts in con.execute("select task_id,author,body,created_at from task_comments order by created_at,id"):
            com.setdefault(tid, []).append((author, text, ts))
    finally:
        con.close()
    cards = [build_card(r, com.get(r[0], [])) for r in rows]
    counts = {}
    for c in cards:
        counts[c.code] = counts.get(c.code, 0) + 1
    for c in cards:
        if counts[c.code] > 1:
            c["code"] = c.code6
    return cards


def resolve_code(cards, code):
    """Cards whose code (4 or 6 chars, any case) matches."""
    code = re.sub(r"[^0-9A-Za-z]", "", code or "").upper()
    if len(code) < 4:
        return []
    return [c for c in cards if c.id.upper().endswith(code)]


# ---------- duplicate detection ----------
def find_duplicate(cards, link="", company="", title="", location="", keys=()):
    """Return (card, reason) for an existing card that is the same job, else (None, '')."""
    keys = {k for k in keys if k}
    n = norm_url(link)
    ids = req_ids(link)
    cn, tn = company_norm(company), alnum(title)
    for c in cards:
        if c.idempotency_key and c.idempotency_key in keys:
            return c, "same idempotency key"
        cl = norm_url(c.link)
        if n and cl and n == cl:
            if link_has_id(link):
                return c, "same link"
            c1 = loc_norm((location or "").split(",")[0])
            c2 = loc_norm((c.location or "").split(",")[0])
            if (not tn or tn == alnum(c.title)) and not (c1 and c2 and c1 != c2):
                return c, "same link"
        if ids and (ids & req_ids(c.link)):
            return c, "same requisition id"
    for c in cards:
        ccn, ctn = company_norm(c.company), alnum(c.title)
        if not (cn and tn and ccn and ctn):
            continue
        if not (cn == ccn or (min(len(cn), len(ccn)) >= 5 and (cn.startswith(ccn) or ccn.startswith(cn)))):
            continue
        if tn != ctn:
            continue
        if ids and req_ids(c.link) and not (ids & req_ids(c.link)):
            continue          # both postings carry different requisition ids: different jobs
        c1 = loc_norm((location or "").split(",")[0])
        c2 = loc_norm((c.location or "").split(",")[0])
        if c1 and c2 and c1 != c2:
            continue          # same title, different city: a different job
        return c, "same company and title"
    return None, ""


# ---------- relative dates ----------
def posted_from_ago(text):
    """'3 days ago' -> '~YYYY-MM-DD'; 'today'/'yesterday'; None if not understood."""
    t = re.sub(r"^(re-?posted|posted|updated)\s+", "", (text or "").strip().lower())
    base = datetime.now(tz()).date()
    if t in ("today", "just posted", "just now", "new"):
        return "~" + base.isoformat()
    if t == "yesterday":
        return "~" + (base - timedelta(days=1)).isoformat()
    m = re.match(r"(?:posted\s+)?(\d+)\+?\s*(hour|day|week|month)s?\s+ago", t)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    days = {"hour": 0, "day": n, "week": 7 * n, "month": 30 * n}[unit]
    return "~" + (base - timedelta(days=days)).isoformat()


def norm_posted(v):
    v = (v or "").strip()
    if not v or v.lower() in ("unknown", "n/a", "none", "-"):
        return "unknown"
    if re.match(r"^~?\d{4}-\d{2}-\d{2}$", v):
        return v
    ago = posted_from_ago(v)
    return ago or "unknown"


# ---------- reject log ----------
def rejects_path():
    return state_dir() / "job_rejects.jsonl"


# ------------------------------------------------------------------ link safety
def _ip_ok(ip):
    import ipaddress
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


def public_url(u):
    """True only for http(s) links whose host resolves to public addresses only.

    Literal IPs in any spelling (127.1, 0x7f.0.0.1, ::ffff:127.0.0.1) and names that resolve to private or loopback
    addresses are refused. A redirect or DNS change after this check can't be seen from here, so the model's tools
    are also told to stay on the posted site; treat this as a guard rail, not a sandbox."""
    import socket
    from urllib.parse import urlsplit as us
    try:
        p = us(u)
        h = (p.hostname or "").lower().rstrip(".")
        p.port  # noqa: B018 (raises ValueError on a bad port)
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not h or p.username or p.password:
        return False
    if h == "localhost" or h.endswith((".local", ".localhost", ".internal", ".lan", ".home", ".localdomain")):
        return False
    try:
        infos = socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        # tests (and offline use) can opt in; literal IPs are still checked below
        if os.environ.get("BOARD_ALLOW_UNRESOLVED") == "1" and "." in h:
            return not re.match(r"^[0-9.x]+$", h, re.I)
        return False
    ips = {i[4][0] for i in infos}
    return bool(ips) and all(_ip_ok(ip) for ip in ips)
