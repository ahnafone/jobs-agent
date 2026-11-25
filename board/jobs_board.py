#!/usr/bin/env python3
"""Applications board server: a small web app over the `jobs` kanban board (standard library only).

  python3 jobs_board.py [--port 8765] [--host 127.0.0.1]

Reads the board through jobs_common (read-only sqlite). Every change goes through the same scripts the
assistant uses (jobs_mark.py, jobs_add.py, ...), so the evidence rules, dedupe and gate stay in one place.
It listens on localhost only (reach it from outside through a Cloudflare tunnel, see README). Every /api request needs
a signed-in session: one password, set with board_auth.py. It never shows email bodies.
"""
import argparse
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

HERE = Path(__file__).resolve().parent
SCRIPTS = Path(os.environ.get("JOBS_SCRIPTS_DIR") or HERE.parent / "scripts")
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))
import jobs_common as C  # noqa: E402
import jobs_config as CFG  # noqa: E402
import jobs_export as X  # noqa: E402
import jobs_gate as G  # noqa: E402
import jobs_list as L  # noqa: E402
import board_agent as A  # noqa: E402
import board_auth as AU  # noqa: E402

STATIC = Path(os.environ.get("BOARD_STATIC_DIR") or HERE / "static")
ALLOWED_HOSTS = CFG.allowed_hosts()
INTAKE_JOB_ID = CFG.intake_job_id()
MAX_BODY = 64 * 1024
STAGE_ID = {"New": "new", "Applied": "applied", "Assessment": "interview", "Interview": "interview",
            "Offer": "offer", "Rejected": "rejected", "Parked": "parked"}
STAGE_LABEL = {"new": "New", "applied": "Applied", "interview": "Interview", "offer": "Offer",
               "rejected": "Rejected", "parked": "Not for me"}
SOURCE_LABEL = {"email": "Email", "discord": "Chat", "discord-link": "Chat link", "board": "Pasted link"}
WRITE_LOCK = threading.Lock()
META_LOCK = threading.Lock()
JOBS = {}                 # link-intake and chat jobs, in memory
JOBS_LOCK = threading.Lock()


# ------------------------------------------------------------------ small helpers
def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_date(s):
    try:
        return datetime.strptime((s or "").lstrip("~")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def today_date():
    return datetime.strptime(C.today(), "%Y-%m-%d").date()


def meta_path():
    return C.state_dir() / "board_meta.json"


def load_meta():
    try:
        m = json.loads(meta_path().read_text())
    except (OSError, ValueError):
        m = {}
    m.setdefault("views", [])
    m.setdefault("dismissed", {})
    m.setdefault("chat", [])
    m.setdefault("checkRequestedAt", 0)
    return m


def save_meta(m):
    p = meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=1))
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def update_meta(fn):
    with META_LOCK:
        m = load_meta()
        out = fn(m)
        save_meta(m)
        return out


READ_ONLY_SCRIPTS = {"job_email_lookup.py", "jobs_list.py", "jobs_handover.py"}


def run_script(name, *args, timeout=180):
    """Run one of the jobs_* scripts; returns (returncode, combined output). Writers are serialised by a lock."""
    cmd = [sys.executable, str(SCRIPTS / name), *args]
    try:
        if name in READ_ONLY_SCRIPTS:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(SCRIPTS))
        else:
            with WRITE_LOCK:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(SCRIPTS))
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return p.returncode, (p.stdout + p.stderr).strip()


def split_sentences(text, per=2, limit=4):
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]
    paras = [" ".join(parts[i:i + per]) for i in range(0, len(parts), per)]
    return paras[:limit]


def split_reqs(text):
    items = [s.strip(" -•\t") for s in re.split(r";|\n|(?<=[.!?])\s+", text or "") if s.strip(" -•\t")]
    return items[:8]


def work_style(location):
    t = (location or "").lower()
    if "remote" in t:
        return "Remote"
    if "hybrid" in t:
        return "Hybrid"
    if re.search(r"on-?site|in[- ]person|on location", t):
        return "On-site"
    return ""


def city_of(location):
    t = re.sub(r"(?i)\b(remote|hybrid|on-?site|in[- ]person)\b[\s,()/-]*", "", location or "").strip(" ,()-/")
    return "" if t.lower() in ("not stated", "unknown", "n/a") else t


def parse_posted(v):
    v = (v or "").strip()
    if not v or v.lower().startswith("unknown"):
        return {"date": "", "approx": False}
    approx = v.startswith("~")
    d = to_date(v)
    return {"date": d.isoformat() if d else "", "approx": approx}


def days_between(a, b):
    return (b - a).days


# ------------------------------------------------------------------ resume state (written by resume_request.py)
RESUME_KINDS = {"resume_pdf": "application/pdf", "letter_pdf": "application/pdf",
                "resume_docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "letter_docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def resume_state_raw(code):
    p = C.state_dir() / "resume_requests" / f"{re.sub(r'[^0-9A-Za-z]', '', code)}.json"
    try:
        s = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return s if isinstance(s, dict) else None


RESUME_CHILDREN = {}
RESUME_LOCK = threading.Lock()
CHILD_STARTED = {}
WORKING_STATUSES = ("queued", "reading", "writing_resume", "checking")


def _resume_lock_alive(code):
    return bool(_resume_lock_pid(code))


def _resume_lock_pid(code):
    """The pid in a live run's lock file (a run started by the board, the CLI or the assistant), or 0."""
    try:
        pid = int((C.state_dir() / "resume_requests" / f"{re.sub(r'[^0-9A-Za-z]', '', code)}.lock").read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return 0
    return pid if _is_resume_script(pid, code) else 0       # a reused pid of some other program is not a run


scrub_paths = C.scrub_paths      # local file paths never go to the browser


def resume_state(code):
    s = resume_state_raw(code)
    child = RESUME_CHILDREN.get(code)
    running = child is not None and child.poll() is None
    if s is None and not running:
        return None
    s = s or {}
    status, step, error = s.get("status"), s.get("step", ""), s.get("error", "")
    started = CHILD_STARTED.get(code, 0)
    try:
        updated = datetime.fromisoformat(str(s.get("updated_at", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        updated = 0
    if running and status not in WORKING_STATUSES:
        status, step = "queued", "starting"       # the script hasn't written its first status yet
    elif (child is not None and not running and child.returncode not in (0, 3, None) and updated < int(started) - 1
          and not _resume_lock_alive(code) and status not in ("ready", "needs_input", "approved")):
        status, step, error = "failed", "didn't start", "It couldn't start. Try again."   # died before its first status
    elif status in WORKING_STATUSES and not running and not _resume_lock_alive(code):
        try:
            age = time.time() - datetime.fromisoformat(str(s.get("updated_at", "")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            age = 1e9
        if age > 20:                              # nothing is working on it any more (restart, crash)
            status, step, error = "failed", "stopped", "It was interrupted before it finished. Try again."
    files = {k: Path(v).name for k, v in (s.get("files") or {}).items() if k in RESUME_KINDS and v}
    versions = [v for v in (s.get("versions") or []) if isinstance(v, dict)]
    last = versions[-1] if versions else {}
    can_undo = status in ("ready", "approved") and last.get("kind") == "revision" and bool(last.get("base"))
    return {"status": status, "step": step, "questions": s.get("questions") or [],
            "files": files, "error": scrub_paths(error), "errors": [scrub_paths(e) for e in (s.get("errors") or [])][:10], "attempt": s.get("attempt", 0),
            "feedback": s.get("feedback", ""), "updated": s.get("updated_at", ""),
            "version": int(s.get("version") or 0), "canUndo": can_undo,
            "undoTo": int(last.get("base") or 0) if can_undo else 0}


# ------------------------------------------------------------------ resume chat (written by resume_request.py)
def _req_dir():
    return C.state_dir() / "resume_requests"


def _safe_code(code):
    return re.sub(r"[^0-9A-Za-z]", "", str(code or ""))[:12]


def resume_chat_raw(code):
    try:
        d = json.loads((_req_dir() / f"{_safe_code(code)}.chat.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    m = d.get("messages") if isinstance(d, dict) else None
    return [x for x in m if isinstance(x, dict) and isinstance(x.get("text"), str)] if isinstance(m, list) else []


def resume_read_at(code):
    try:
        return (_req_dir() / f"{_safe_code(code)}.read").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def mark_resume_read(code):
    d = _req_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{_safe_code(code)}.read"
    # a name of its own: the writer script saves <CODE>.json through <CODE>.json.<pid>.tmp and must never share one
    tmp = p.with_name(f"{p.name}.board-{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_text(datetime.now(timezone.utc).isoformat(timespec="milliseconds"), encoding="utf-8")
    os.replace(tmp, p)


def chat_message_json(m):
    out = {"role": "user" if m.get("role") == "user" else "agent", "text": scrub_paths(C.clean(m.get("text"), 2000)),
           "ts": str(m.get("ts") or "")}
    if isinstance(m.get("version"), int):
        out["version"] = m["version"]
    if m.get("quiet"):
        out["quiet"] = True
    return out


def unread_count(msgs, read_at):
    return sum(1 for m in msgs if m.get("role") != "user" and not m.get("quiet") and str(m.get("ts") or "") > read_at)


def messages_summary(cards=None):
    """One row per job that has a resume request: for the Messages screen and the unread badge."""
    try:
        files = [p for p in _req_dir().glob("*.json") if p.stem.isalnum()]
    except OSError:
        return {"threads": [], "unread": 0}
    by_code = {c.code: c for c in (cards if cards is not None else C.load_cards())}   # archived jobs have no chat to open
    threads, total = [], 0
    for p in files:
        card = by_code.get(p.stem)
        s = resume_state_raw(p.stem)
        if card is None or s is None:
            continue
        rs = resume_state(p.stem) or {}
        msgs = resume_chat_raw(p.stem)
        unread = unread_count(msgs, resume_read_at(p.stem))
        shown = [m for m in msgs if m.get("text")]
        last = shown[-1] if shown else None
        total += unread
        threads.append({"code": card.code, "company": card.company or "", "title": card.title or "",
                        "status": rs.get("status") or "", "version": rs.get("version", 0), "unread": unread,
                        "snippet": scrub_paths(C.one_line(last["text"], 140)) if last else "",
                        "from": ("you" if last and last.get("role") == "user" else "agent") if last else "",
                        "ts": (last.get("ts") if last else "") or s.get("updated_at", "")})
    threads.sort(key=lambda t: t["ts"], reverse=True)
    return {"threads": threads, "unread": total}


def resume_file(code, kind):
    """(path, content-type) of a finished resume/letter file, only from the user's own Resumes / Cover Letters folders."""
    if kind not in RESUME_KINDS:
        raise ApiError("Not found.", 404)
    s = resume_state_raw(code) or {}
    v = (s.get("files") or {}).get(kind)
    if not v:
        raise ApiError("That file isn't ready.", 404)
    f = Path(v).resolve()
    base = C.export_dir().resolve()
    if not f.is_file() or not any(d in f.parents for d in (base / "Resumes", base / "Cover Letters")):
        raise ApiError("That file isn't there any more.", 404)
    return f, RESUME_KINDS[kind]


# ------------------------------------------------------------------ card -> JSON
def timeline(c):
    ev = [(c.added, 0, "Added to the board", "you" if c.source == "board" else "assistant")]
    if c.applied:
        ev.append((c.applied_date, 1, "Applied", "you" if any(e["by_you"] and e["stage"] != "new" for e in c.stage_events) else ""))
    for e in c.stage_events:
        ev.append((e["date"], 2, f"Moved to {STAGE_LABEL.get(e['stage'], e['stage'])}" + (f" ({e['text']})" if e["text"] else ""),
                   "you" if e["by_you"] else "assistant"))
    for r in c.responses:
        ev.append((r["date"], 3, f"{r['type'].replace('-', ' ').capitalize()}: {r['text']}" + (f" (by {r['deadline']})" if r["deadline"] else ""),
                   "you" if r["by_you"] else "assistant"))
    ev.sort(key=lambda e: (e[0] or "", e[1]))
    return [{"date": d, "label": lbl, "by": by} for d, _, lbl, by in ev if d]


def bot_moved(c):
    """True when the newest change to the card's stage came from the assistant (not the user's click) recently."""
    last = None
    for a in c.activity:
        if a["kind"] in ("response", "stage", "applied"):
            last = a
    if not last or last["by_you"] or c.stage == "New":
        return False
    return (C.now_ts() - (last["ts"] or 0)) < 21 * 86400


def card_json(c, dups=None):
    stage = STAGE_ID.get(c.stage, "new")
    parked_reason = ""
    if stage == "parked":
        ev = [e for e in c.stage_events if e["stage"] == "parked"]
        parked_reason = (ev[-1]["text"] if ev else "") or c.review_note or "Set aside"
    paras = split_sentences(c.description) or ([c.summary] if c.summary else [])
    resp = [{"date": r["date"], "type": ("info" if r["type"] == "info-request" else r["type"]), "note": r["text"],
             "deadline": r["deadline"], "byYou": r["by_you"]} for r in c.responses]
    return {
        "code": c.code, "id": c.id, "title": c.title, "company": c.company, "stage": stage,
        "fit": c.fit, "fitReason": c.reason,
        "workStyle": work_style(c.location), "city": city_of(c.location), "location": c.location,
        "pay": "" if re.match(r"(?i)^\s*(not stated|unknown|n/?a|none)\b", c.pay or "") or not c.pay else c.pay,
        "posted": parse_posted(c.posted), "added": c.added, "appliedOn": c.applied_date or "",
        "source": SOURCE_LABEL.get(c.source, re.sub(r"\s*\(.*$", "", c.source).capitalize()), "url": c.link,
        "summary": c.summary, "description": paras, "requirements": split_reqs(c.requirements),
        "responses": resp, "notes": [{"date": n["date"], "text": n["text"]} for n in c.notes],
        "timeline": timeline(c), "updatedByAssistant": bot_moved(c), "reason": parked_reason,
        "action": "" if (c.action or "").lower() in ("none", "none stated", "") else c.action,
        "resume": resume_state(c.code), "possibleDuplicateOf": (dups or {}).get(c.code, []),
    }


def inbox_json(c):
    j = card_json(c)
    j["eligible"] = True
    j["eligibility"] = "Passes your checks"
    j["found"] = c.review_note or "Found by your assistant"
    return j


def ineligible_entries(cards):
    """Jobs the gate turned away (never became cards), newest first, minus anything now on the board."""
    out, seen = [], set()
    for x in X.read_rejects(120):
        key = hashlib.sha1(f"{x.get('title','')}|{x.get('company','')}|{x.get('link','')}".encode()).hexdigest()[:10]
        if key in seen:
            continue
        seen.add(key)
        d, _ = C.find_duplicate(cards, x.get("link", ""), x.get("company", ""), x.get("title", ""), x.get("location", ""))
        if d is not None:
            continue
        out.append({"id": key, "title": x.get("title", ""), "company": x.get("company", ""), "location": x.get("location", ""),
                    "url": x.get("link", ""), "reason": x.get("reason", ""), "added": x.get("date", ""),
                    "source": SOURCE_LABEL.get(x.get("source", ""), x.get("source", "") or "Email"), "stage": "parked",
                    "ineligible": True, "workStyle": work_style(x.get("location")), "city": city_of(x.get("location"))})
        if len(out) >= 40:
            break
    return out


# ------------------------------------------------------------------ attention strip, stats, last check
def attention(cards, meta):
    out, today = [], today_date()
    dis = meta.get("dismissed", {})
    for c in cards:
        if c.stage in ("Rejected", "Parked"):
            continue
        for r in c.responses:
            dl = to_date(r.get("deadline"))
            if dl and dl >= today:
                out.append({"id": f"{c.code}:deadline:{r['deadline']}", "code": c.code, "kind": "deadline",
                            "title": f"{r['type'].replace('-', ' ').capitalize()} for {c.title} at {c.company}",
                            "text": r["text"] or "Reply needed", "due": r["deadline"], "daysLeft": days_between(today, dl)})
        lr = c.last_response
        if lr and lr["type"] in ("interview", "assessment", "info-request", "offer") and not lr.get("deadline"):
            d = to_date(lr["date"])
            if d and 0 <= days_between(d, today) <= 10 and not any(a["code"] == c.code and a["kind"] == "deadline" for a in out):
                out.append({"id": f"{c.code}:reply:{lr['date']}:{lr['type']}", "code": c.code, "kind": "reply",
                            "title": f"{lr['type'].replace('-', ' ').capitalize()} from {c.company}", "text": lr["text"], "due": ""})
        if c.stage == "Applied" and not c.responses:
            d = to_date(c.applied_date)
            if d and days_between(d, today) >= 10:
                out.append({"id": f"{c.code}:followup:{c.applied_date}", "code": c.code, "kind": "followup",
                            "title": f"{c.title} at {c.company}", "text": f"Applied {days_between(d, today)} days ago, no reply yet", "due": ""})
    now = C.now_ts()
    keep = []
    for a in out:
        until = dis.get(a["id"])
        if until == "forever" or (isinstance(until, (int, float)) and until > now):
            continue
        keep.append(a)
    keep.sort(key=lambda a: (a["kind"] != "deadline", a.get("due") or "9999", a["id"]))
    return keep[:12]


def stats(cards):
    today = today_date()
    applied = [c for c in cards if c.applied]
    monday = today - timedelta(days=today.weekday())
    weeks = []
    for i in range(7, -1, -1):
        start = monday - timedelta(weeks=i)
        n = sum(1 for c in applied if (to_date(c.applied_date) or date.min) >= start and (to_date(c.applied_date) or date.min) < start + timedelta(days=7))
        weeks.append({"weekOf": start.isoformat(), "applied": n})
    replied = [c for c in applied if any(r["type"] != "info-request" for r in c.responses)]
    gaps = []
    for c in replied:
        a, r = to_date(c.applied_date), to_date(sorted(c.responses, key=lambda r: r["date"])[0]["date"])
        if a and r and r >= a:
            gaps.append((r - a).days)
    return {"weeks": weeks, "applied": len(applied), "replied": len(replied),
            "replyRate": round(100 * len(replied) / len(applied)) if applied else 0,
            "medianDaysToReply": statistics.median(gaps) if gaps else None}


def last_checked():
    """Epoch seconds of the last finished email check (the intake cron's last run), from Hermes' own job list."""
    try:
        d = json.loads((Path.home() / ".hermes" / "cron" / "jobs.json").read_text())
        jobs = d.get("jobs", d) if isinstance(d, dict) else d
        for j in jobs:
            if INTAKE_JOB_ID and j.get("id") == INTAKE_JOB_ID:
                s = j.get("last_run_at")
                if s:
                    return int(datetime.fromisoformat(s).timestamp())
    except (OSError, ValueError, TypeError):
        pass
    return None


def build_state():
    meta = load_meta()
    cards = C.load_cards()
    dup_map = {}
    for grp in L.dupes([c for c in cards if c.review != "pending"]):
        for c in grp:
            dup_map[c.code] = [o.code for o in grp if o.code != c.code]
    board = [c for c in cards if c.review != "pending"]
    inbox = [c for c in cards if c.review == "pending"]
    checked = last_checked()
    return {
        "today": C.today(), "now": C.now_ts(),
        "cards": [card_json(c, dup_map) for c in board],
        "inbox": [inbox_json(c) for c in inbox],
        "ineligible": ineligible_entries(cards),
        "attention": attention(board, meta),
        "stats": stats(board),
        "lastChecked": checked, "checkRequestedAt": meta.get("checkRequestedAt") or 0,
        "views": meta["views"], "messagesUnread": messages_summary(cards)["unread"],
    }


def activity(limit=60):
    cards = C.load_cards(include_archived=True)
    rows = []
    for c in cards:
        last_stage_seq = c.stage_events[-1]["seq"] if c.stage_events else None
        by_seq = {e["seq"]: e for e in c.stage_events}
        for a in c.activity:
            row = {"ts": a["ts"], "code": c.code, "title": c.title, "company": c.company, "by": "you" if a["by_you"] else "assistant",
                   "kind": a["kind"], "text": a["text"], "undo": None}
            if a["kind"] == "stage":
                ev = by_seq[a["seq"]]
                row["text"] = f"Moved to {STAGE_LABEL.get(ev['stage'], ev['stage'])}" + (f" ({ev['text']})" if ev["text"] else "")
                last_resp_after = any(r["seq"] > ev["seq"] for r in c.responses if r["type"] != "info-request")
                if ev["seq"] == last_stage_seq and ev["from"] and not last_resp_after and c.status != "archived":
                    row["undo"] = {"code": c.code, "to": ev["from"] if ev["from"] in STAGE_LABEL else STAGE_ID.get(ev["from"].capitalize(), "new")}
            elif a["kind"] == "response":
                row["text"] = f"Reply recorded: {a['text']}"
            elif a["kind"] == "applied":
                row["text"] = "Marked as applied"
            elif a["kind"] == "review":
                row["text"] = "Inbox: " + a["text"]
            elif a["kind"] == "note":
                row["text"] = f"Note: {a['text']}"
            rows.append(row)
    rows.sort(key=lambda r: r["ts"] or 0, reverse=True)
    return rows[:limit]


# ------------------------------------------------------------------ actions
class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def need_card(code):
    found = C.resolve_code(C.load_cards(), code or "")
    if not found:
        raise ApiError("I can't find that card.", 404)
    if len(found) > 1:
        raise ApiError("More than one card matches that code.", 409)
    return found[0]


def act_stage(code, stage, note="", undo=False):
    if stage not in STAGE_LABEL:
        raise ApiError("That stage doesn't exist.")
    card = need_card(code)
    prev = STAGE_ID.get(card.stage, "new")
    rc, out = run_script("jobs_mark.py", card.code6 if card.code != card.code6 else card.code, "--actor", "you", "--stage", stage,
                         "--note=" + ("undo" if undo else C.one_line(note, 200)))
    if rc != 0:
        raise ApiError("I couldn't save that change. Try again.", 500)
    return {"ok": True, "message": f"Moved to {STAGE_LABEL[stage]}.", "undo": {"code": card.code, "to": prev}}


def act_note(code, text):
    text = C.clean(text, 1500)
    if not text:
        raise ApiError("Write something first.")
    card = need_card(code)
    rc, _ = run_script("jobs_mark.py", card.code, "--actor", "you", "--add-note=" + text)
    if rc != 0:
        raise ApiError("I couldn't save the note.", 500)
    return {"ok": True, "message": "Note saved."}


def act_review(code, decision, reason=""):
    card = need_card(code)
    if card.review != "pending" and decision != "pending":
        raise ApiError("That one is already decided.")
    if decision == "pending" and (card.review == "pending" or card.stage not in ("New", "Parked")):
        raise ApiError("That card has moved on, so I can't send it back to the Inbox.")
    rc, _ = run_script("jobs_mark.py", card.code, "--actor", "you", "--review", decision, "--note=" + C.one_line(reason, 200))
    if rc != 0:
        raise ApiError("I couldn't save that.", 500)
    return {"ok": True, "message": "Added to your board." if decision == "accepted" else "Declined.",
            "undo": {"code": card.code, "review": "pending"}}


def act_merge(keep, drop):
    k, d = need_card(keep), need_card(drop)
    if k.id == d.id:
        raise ApiError("Pick two different cards.")
    rc, out = run_script("jobs_mark.py", d.code, "--actor", "you", "--archive", "--note=" + f"duplicate of [{k.code}] (merged by you)")
    if rc != 0:
        raise ApiError("I couldn't merge them.", 500)
    return {"ok": True, "message": f"Merged into [{k.code}]."}


def act_add_anyway(entry_id):
    for x in X.read_rejects(120):
        key = hashlib.sha1(f"{x.get('title','')}|{x.get('company','')}|{x.get('link','')}".encode()).hexdigest()[:10]
        if key == entry_id:
            return add_card({"title": x.get("title", ""), "company": x.get("company", ""), "location": x.get("location", ""),
                             "link": x.get("link", ""), "fit": 50, "reason": f"Added anyway by you. The checks said: {x.get('reason','')}"},
                            force=True)
    raise ApiError("I can't find that one any more.", 404)


def add_card(j, force=False, review=False):
    """Create a card from a dict via jobs_add.py. Returns {'outcome': CREATED|REJECTED|ALREADY-..., 'line': str, 'code': str}."""
    args = ["--title=" + C.one_line(j.get("title"), 200), "--company=" + (C.one_line(j.get("company"), 120) or "Unknown employer"),
            "--fit=" + str(max(0, min(100, int(j.get("fit") or 50)))), "--reason=" + (C.one_line(j.get("reason"), 400) or "Added from the board."),
            "--source=board"]
    for opt, key in (("--location", "location"), ("--pay", "pay"), ("--schedule", "schedule"), ("--requirements", "requirements"),
                     ("--summary", "summary"), ("--description", "description")):
        if j.get(key):
            args.append(f"{opt}=" + C.one_line(j[key], 1300))
    link = (j.get("link") or "").strip()
    if link and C.norm_url(link):
        args.append("--link=" + link)
    posted = (j.get("posted") or "").strip()
    if re.match(r"^~?\d{4}-\d{2}-\d{2}$", posted):
        args.append("--posted=" + posted)
    elif posted and posted.lower() != "unknown":
        args.append("--posted-ago=" + posted)
    if force:
        args.append("--force")
    if review:
        args.append("--review")
    rc, out = run_script("jobs_add.py", *args)
    line = out.splitlines()[-1] if out else ""
    m = re.match(r"^(CREATED|REJECTED|ALREADY-TRACKED|ALREADY-APPLIED|ALREADY-ARCHIVED)\b", line)
    if not m:
        raise ApiError("I couldn't add that job.", 500)
    code = (re.search(r"\[([0-9A-F]{4,6})\]", line) or [None, ""])[1]
    return {"outcome": m.group(1), "line": line, "code": code, "ok": m.group(1) == "CREATED"}


# ------------------------------------------------------------------ link intake
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)


public_url = C.public_url


def new_job(kind):
    jid = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        if len(JOBS) > 60:
            for k in sorted(JOBS, key=lambda k: JOBS[k]["created"])[:20]:
                JOBS.pop(k, None)
        JOBS[jid] = {"id": jid, "kind": kind, "state": "working", "created": time.time(), "items": [], "results": []}
    return JOBS[jid]


def job_view(job):
    with JOBS_LOCK:
        return json.loads(json.dumps({k: v for k, v in job.items() if k != "private"}))


def eligibility_of(item):
    res = G.evaluate(item.get("title", ""), item.get("company", ""), item.get("location", ""),
                     "\n".join(x for x in (item.get("requirements"), item.get("description"), item.get("summary")) if x),
                     "board", req_text=item.get("requirements"))
    return res


def process_url(job, item, force=False):
    url = item["url"]
    item["state"] = "working"
    cards = C.load_cards(include_archived=True)
    dup, why = C.find_duplicate(cards, url, "", "", "", [C.url_key(url)])
    if dup is not None and C.link_has_id(url):
        item.update(state="done", verdict="tracked", headline="Already tracked", code=dup.code,
                    note=f"[{dup.code}] {dup.title} - {dup.company} is already on your board ({dup.stage}).")
        return
    try:
        out = A.run_model(A.read_link_prompt(url), key=job["id"], timeout=300)
    except A.Cancelled:
        item.update(state="done", verdict="error", headline="Stopped", note="You stopped this one.")
        return
    except RuntimeError as ex:
        item.update(state="done", verdict="error", headline="Couldn't read it", note=str(ex))
        return
    data = A.extract_json(out)
    if not data or data.get("kind") == "error" or data.get("kind") not in ("job", "listing"):
        reason = (data or {}).get("reason") or "The page didn't look like a job posting."
        item.update(state="done", verdict="error", headline="Couldn't read it", note=C.one_line(reason, 200))
        return
    if data["kind"] == "listing":
        jobs = []
        for i, o in enumerate((data.get("openings") or [])[:40]):
            if not isinstance(o, dict) or not o.get("title"):
                continue
            link = (o.get("link") or "").strip()
            link = link if link and public_url(link) else ""
            card = {"title": o["title"], "company": data.get("source") or "", "location": o.get("location", ""),
                    "requirements": o.get("requirements", "")}
            res = eligibility_of(card)
            ok = res["verdict"] != "reject"
            jobs.append({"id": f"{item['n']}-{i}", "title": C.one_line(o["title"], 160), "team": C.one_line(o.get("team", ""), 60),
                         "location": C.one_line(o.get("location", ""), 80), "link": link or url, "eligible": ok,
                         "reason": "" if ok else res["reason"]})
        item.update(state="done", verdict="listing", headline=f"Found {len(jobs)} openings",
                    listing={"source": C.one_line(data.get("source") or urlsplit(url).netloc, 100), "found": len(jobs), "jobs": jobs})
        return
    # a single job
    card = {k: C.one_line(data.get(k), 1300) for k in ("title", "company", "location", "pay", "schedule", "posted", "requirements",
                                                       "summary", "description", "reason")}
    card["link"] = url
    try:
        card["fit"] = max(0, min(100, int(data.get("fit", 50))))
    except (TypeError, ValueError):
        card["fit"] = 50
    if not card["title"]:
        item.update(state="done", verdict="error", headline="Couldn't read it", note="No job title found on the page.")
        return
    res = eligibility_of(card)
    summary = {"title": card["title"], "company": card["company"] or "Unknown employer", "fit": card["fit"], "fitReason": card["reason"],
               "pay": card["pay"], "workStyle": work_style(card["location"]), "city": city_of(card["location"]), "url": url}
    if res["verdict"] == "reject" and not force:
        G.log_reject(card["title"], card["company"], card["location"], url, res["reason"], "board")
        item.update(state="done", verdict="ineligible", headline="Not eligible: " + res["reason"],
                    note="I can still add it if you want to apply anyway.", item=summary, eligibility=res["reason"], pending=card)
        return
    out = add_card(card, force=force)
    if out["outcome"] == "CREATED":
        item.update(state="done", verdict="added", headline="Added to New", code=out["code"], item=summary, eligibility="Passes your checks")
    else:
        item.update(state="done", verdict="tracked", headline="Already tracked", code=out["code"], item=summary, note=out["line"])


def run_add_links(job, urls, force_urls=()):
    try:
        for i, u in enumerate(urls):
            item = job["items"][i]
            if job.get("cancelled"):
                item.update(state="done", verdict="error", headline="Stopped", note="Stopped before this one.")
                continue
            process_url(job, item, force=u in force_urls)
        job["state"] = "done"
    except Exception as ex:  # noqa: BLE001
        job["state"] = "error"
        job["error"] = "Something went wrong while reading the links."
        sys.stderr.write(f"add-links failed: {ex!r}\n")


def start_add_links(text, force_urls=()):
    urls = []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,;")
        if u not in urls:
            urls.append(u)
    if not urls:
        raise ApiError("Paste at least one link that starts with http.")
    if len(urls) > 10:
        raise ApiError("That's a lot of links. Try 10 or fewer at a time.")
    bad = [u for u in urls if not public_url(u)]
    if bad:
        raise ApiError("One of those isn't a public web link: " + bad[0][:80])
    job = new_job("add")
    job["items"] = [{"n": i, "url": u, "label": re.sub(r"^https?://(www\.)?", "", u)[:90], "state": "waiting"} for i, u in enumerate(urls)]
    threading.Thread(target=run_add_links, args=(job, urls, set(force_urls)), daemon=True).start()
    return job


# ------------------------------------------------------------------ chat
EMAIL_WORDS = re.compile(r"(?i)\b(e-?mails?|inbox|repl(?:y|ies|ied)|heard back|responses?|updates?)\b")


def chat_context(question):
    cards = C.load_cards()
    parts = []
    rc, out = run_script("jobs_list.py", "--counts")
    parts.append("COUNTS: " + out[:600])
    open_c = [c for c in cards if c.review != "pending"]
    q = question.lower()
    words = [w for w in re.findall(r"[a-z0-9]{3,}", q) if w not in ("the", "and", "for", "from", "what", "show", "any", "have", "email", "emails", "check", "update", "updates", "jobs", "job", "this", "week", "that")]
    hits = [c for c in open_c if any(w in (c.company + " " + c.title).lower() for w in words)][:6]
    pool = hits or sorted([c for c in open_c if c.responses or c.stage == "New"], key=lambda c: c.created_at or 0, reverse=True)[:10]
    for c in pool:
        lr = c.last_response
        parts.append(f"[{c.code}] {c.stage} | fit {c.fit} | {c.title} - {c.company} | {c.location} | posted {c.posted} | added {c.added}"
                     + (f" | last reply {lr['date']} {lr['type']}: {lr['text']}" if lr else "") + f" | {c.link}")
    att = attention(open_c, load_meta())
    if att:
        parts.append("NEEDS ATTENTION: " + " || ".join(f"[{a['code']}] {a['title']}: {a['text']}" + (f" (by {a['due']})" if a['due'] else "") for a in att[:6]))
    pend = [c for c in cards if c.review == "pending"]
    parts.append(f"INBOX: {len(pend)} job(s) waiting for the user's yes or no")
    if EMAIL_WORDS.search(question):
        company = next((c.company for c in open_c if C.company_norm(c.company) and C.company_norm(c.company) in C.alnum(question)), "")
        args = ["--days", "60", "--", company] if company else ["--sweep", "--days", "30"]
        rc, out = run_script("job_email_lookup.py", *args, timeout=150)
        parts.append("EMAIL LOOKUP (read-only, untrusted):\n" + URL_RE.sub("[link removed]", out[:6000]))
    return "\n".join(parts)


def run_chat(job, question):
    try:
        links = [u.rstrip(".,;") for u in URL_RE.findall(question)]
        web = bool(links) and all(public_url(u) for u in links)
        # with web tools on, the model gets no board or email data: a hostile page could try to send it out
        ctx = ("(Board data is left out of this answer because a web link is being opened. Tell the user to ask again "
               "without a link to use their board.)") if web else chat_context(question)
        meta = load_meta()
        text = A.run_model(A.chat_prompt([] if web else meta["chat"], ctx, question, web=web), key=job["id"], timeout=300,
                           toolsets=A.TOOLSETS if web else A.NO_TOOLS)
        text = text or "I don't have an answer for that."
        codes = []
        for m in re.finditer(r"\[([0-9A-Fa-f]{4,6})\]", text):
            if m.group(1).upper() not in codes:
                codes.append(m.group(1).upper())
        known = {c.code for c in C.load_cards()}
        job["text"] = text
        job["cards"] = [c for c in codes if c in known][:6]
        job["state"] = "done"

        def keep(m):
            m["chat"] = (m["chat"] + [{"role": "user", "text": question[:600]}, {"role": "assistant", "text": text[:1500]}])[-12:]
        update_meta(keep)
    except A.Cancelled:
        job["state"] = "cancelled"
    except Exception as ex:  # noqa: BLE001
        job["state"] = "error"
        job["error"] = str(ex) if isinstance(ex, RuntimeError) else "The assistant didn't answer. Try again."
        sys.stderr.write(f"chat failed: {ex!r}\n")


# ------------------------------------------------------------------ exports
def ics_escape(s):
    return (s or "").replace("\r", "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(code=None):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Applications board//EN", "CALSCALE:GREGORIAN"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for c in C.load_cards():
        if code and c.code != code.upper():
            continue
        for r in c.responses:
            d = to_date(r.get("deadline"))
            if not d:
                continue
            nxt = d + timedelta(days=1)
            lines += ["BEGIN:VEVENT", f"UID:{c.id}-{r['date']}-{r['type']}@applications-board",
                      f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{nxt.strftime('%Y%m%d')}",
                      f"SUMMARY:{ics_escape(r['type'].replace('-', ' ').capitalize() + ': ' + c.title + ' - ' + c.company)}",
                      f"DESCRIPTION:{ics_escape(r['text'] + (' ' + c.link if c.link else ''))}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def build_csv_text():
    cards = [c for c in C.load_cards() if c.review != "pending"]
    return X.build_csv(X.rows_for(cards), X.read_rejects(120))


# ------------------------------------------------------------------ HTTP
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


COOKIE = "board"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
LOGIN_LOCK = threading.Lock()      # one password check at a time, so parallel guesses can't slip past the lockout


class Handler(BaseHTTPRequestHandler):
    timeout = 30   # a client that stalls mid-request can't hold a thread forever
    server_version = "ApplicationsBoard"
    sys_version = ""
    _set_cookie = None

    def log_message(self, fmt, *args):  # quiet, never log request bodies, and no raw control characters from a request line
        msg = re.sub(r"[^\x20-\x7e]", lambda m: "\\x%02x" % ord(m.group()), fmt % args)
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), msg))

    # -- plumbing
    def host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
        return host in ALLOWED_HOSTS

    # -- sign-in
    def host_name(self):
        return (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()

    def secure_cookie(self):
        return self.host_name() not in LOOPBACK      # the public address is HTTPS; localhost is plain http

    def cookie_name(self):
        return ("__Host-" if self.secure_cookie() else "") + COOKIE

    def make_cookie(self, token, max_age):
        c = f"{self.cookie_name()}={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict"
        return c + "; Secure" if self.secure_cookie() else c

    def session_token(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == self.cookie_name():
                return v
        return ""

    def client_ip(self):
        peer = self.client_address[0]
        if peer in ("127.0.0.1", "::1"):                  # came through the tunnel on this machine
            fwd = (self.headers.get("CF-Connecting-IP") or "").strip()
            try:
                peer = fwd = str(ipaddress.ip_address(fwd))
            except ValueError:
                pass
        try:
            ip = ipaddress.ip_address(peer)
            ip = getattr(ip, "ipv4_mapped", None) or ip
            if ip.version == 4:
                return str(ip)
            if ip.version == 6:                                 # one person can hold a whole /64, so count the block
                return str(ipaddress.ip_network(peer + "/64", strict=False))
        except ValueError:
            pass
        return peer

    def authed(self):
        ok, renewed = AU.check_session(self.session_token())
        if ok and renewed:
            self._set_cookie = self.make_cookie(self.session_token(), AU.SESSION_SECONDS)
        return ok

    def login(self):
        body = self.read_json()
        if not AU.configured():
            sys.stderr.write("sign-in is off: no password set. Run: python3 ~/.hermes/board/board_auth.py set-password\n")
            if self.secure_cookie():
                raise ApiError("Sign-in isn't available right now.", 503)
            raise ApiError("No password is set yet. In a terminal on this computer, run: "
                           "python3 ~/.hermes/board/board_auth.py set-password", 503)
        ip = self.client_ip()
        with LOGIN_LOCK:
            wait = AU.wait_for(ip)
            if wait:
                mins = max(1, (wait + 59) // 60)
                return self.send(429, {"ok": False, "wait": wait, "login": True,
                                       "error": f"Too many wrong passwords. Try again in {mins} minute{'s' if mins != 1 else ''}."},
                                 extra={"Retry-After": str(wait)})
            good = AU.check_password(body.get("password"))
            if good:
                AU.record_success(ip)
            else:
                AU.record_failure(ip)
        if not good:
            sys.stderr.write(f"sign-in failed from {ip}\n")
            return self.send(401, {"ok": False, "login": True, "error": "That password isn't right."})
        self._set_cookie = self.make_cookie(AU.new_session(), AU.SESSION_SECONDS)
        sys.stderr.write(f"signed in from {ip}\n")
        return self.send(200, {"ok": True})

    def logout(self):
        AU.end_session(self.session_token())
        sys.stderr.write(f"signed out from {self.client_ip()}\n")
        self._set_cookie = self.make_cookie("", 0)
        return self.send(200, {"ok": True})

    def send(self, status, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if "Content-Security-Policy" not in (extra or {}):
            self.send_header("Content-Security-Policy", CSP)
        extra = extra or {}
        if "Cache-Control" not in extra:
            self.send_header("Cache-Control", "no-store")
        for k, v in extra.items():
            self.send_header(k, v)
        if self._set_cookie:
            self.send_header("Set-Cookie", self._set_cookie)
        if self.secure_cookie():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def fail(self, status, message):
        self.send(status, {"ok": False, "error": message})

    def read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError("That request wasn't valid.", 400)
        if n < 0:
            raise ApiError("That request wasn't valid.", 400)
        if n > MAX_BODY:
            raise ApiError("That's too much to send at once.", 413)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            d = json.loads(raw or b"{}")
        except ValueError:
            raise ApiError("I couldn't read that request.")
        return d if isinstance(d, dict) else {}

    def guard(self, write):
        if not self.host_ok():
            self.fail(421, "Unknown host.")
            return False
        if write:
            origin = self.headers.get("Origin")
            if self.headers.get("X-Board") != "1" or (origin and urlsplit(origin).hostname not in ALLOWED_HOSTS):
                self.fail(403, "Request not allowed.")
                return False
        return True

    # -- routes
    def do_GET(self):
        self.route("GET")

    do_HEAD = do_GET

    def do_POST(self):
        self.route("POST")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        write = method != "GET" and method != "HEAD"
        self._set_cookie = None
        if not self.guard(write):
            return
        path = urlsplit("/" + self.path.lstrip("/"))
        parts = [unquote(p) for p in path.path.strip("/").split("/") if p]
        try:
            if parts and parts[0] == "api":
                sub = parts[1:]
                if sub == ["session"] and not write:
                    return self.send(200, {"authed": self.authed()})
                if sub == ["login"] and method == "POST":
                    return self.login()
                if sub == ["logout"] and method == "POST":
                    return self.logout()
                if not self.authed():
                    return self.send(401, {"ok": False, "login": True, "error": "Sign in to see your board."})
                self.api(method, sub, parse_qs(path.query))
            elif method in ("GET", "HEAD"):
                self.static(path.path)
            else:
                self.fail(404, "Not found.")
        except ApiError as e:
            self.fail(e.status, str(e))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"error on {method} {path.path}: {e!r}\n")
            self.fail(500, "Something went wrong on my side. Try again.")

    def api(self, method, p, q):
        n = len(p)
        if method == "GET":
            if p == ["state"]:
                return self.send(200, build_state())
            if p == ["activity"]:
                return self.send(200, {"items": activity(max(1, min(200, int(re.sub(r"[^0-9]", "", (q.get("limit") or ["60"])[0]) or 60))))})
            if p == ["export.csv"]:
                return self.send(200, build_csv_text(), "text/csv; charset=utf-8",
                                 {"Content-Disposition": 'attachment; filename="applications.csv"'})
            if p == ["deadlines.ics"] or (n == 3 and p[0] == "cards" and p[2] == "calendar.ics"):
                code = p[1] if n == 3 else None
                return self.send(200, build_ics(code), "text/calendar; charset=utf-8",
                                 {"Content-Disposition": 'attachment; filename="deadlines.ics"'})
            if n == 2 and p[0] == "add-links":
                job = JOBS.get(p[1])
                if not job:
                    raise ApiError("I can't find that.", 404)
                return self.send(200, job_view(job))
            if n == 2 and p[0] == "chat" and p[1] != "history":
                job = JOBS.get(p[1])
                if not job:
                    raise ApiError("I can't find that.", 404)
                return self.send(200, job_view(job))
            if p == ["chat", "history"]:
                return self.send(200, {"items": load_meta()["chat"]})
            if n == 3 and p[0] == "cards" and p[2] == "handover":
                card = need_card(p[1])
                rc, out = run_script("jobs_handover.py", card.code, "--json")
                try:
                    return self.send(200, json.loads(out))
                except ValueError:
                    raise ApiError("I couldn't build the prompt.", 500)
            if n == 3 and p[0] == "cards" and p[2] == "resume":
                return self.send(200, {"resume": resume_state(need_card(p[1]).code)})
            if p == ["messages"]:
                return self.send(200, messages_summary())
            if n == 4 and p[0] == "cards" and p[2] == "resume" and p[3] == "chat":
                code = need_card(p[1]).code
                return self.send(200, {"messages": [chat_message_json(m) for m in resume_chat_raw(code)],
                                       "resume": resume_state(code), "readAt": resume_read_at(code)})
            if n == 4 and p[0] == "cards" and p[2] == "files":
                f, ctype = resume_file(need_card(p[1]).code, p[3])
                return self.send(200, f.read_bytes(), ctype,
                                 {"Content-Disposition": f'inline; filename="{re.sub(r"[^A-Za-z0-9._-]", "_", f.name)}"',
                                  "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'"})
            raise ApiError("Not found.", 404)

        body = self.read_json()
        if p == ["undo"]:
            if body.get("review"):
                return self.send(200, act_review(body.get("code"), "pending", "undo"))
            return self.send(200, act_stage(body.get("code"), body.get("to"), undo=True))
        if n == 3 and p[0] == "cards" and p[2] == "stage":
            return self.send(200, act_stage(p[1], body.get("stage"), body.get("note", "")))
        if n == 3 and p[0] == "cards" and p[2] == "note":
            return self.send(200, act_note(p[1], body.get("text", "")))
        if n == 3 and p[0] == "inbox" and p[2] in ("accept", "decline"):
            return self.send(200, act_review(p[1], "accepted" if p[2] == "accept" else "declined", body.get("reason", "")))
        if p == ["merge"]:
            return self.send(200, act_merge(body.get("keep"), body.get("drop")))
        if n == 3 and p[0] == "ineligible" and p[2] == "add":
            return self.send(200, act_add_anyway(p[1]))
        if p == ["add-links"]:
            return self.send(200, job_view(start_add_links(body.get("text", ""))))
        if n == 3 and p[0] == "add-links" and p[2] == "anyway":
            # re-run one ineligible single job and add it anyway
            job = JOBS.get(p[1])
            item = job and next((i for i in job["items"] if i.get("n") == body.get("n")), None)
            if not item or not item.get("pending"):
                raise ApiError("I can't find that one any more.", 404)
            out = add_card(item["pending"], force=True)
            item.update(verdict="added" if out["ok"] else "tracked", headline="Added to New" if out["ok"] else "Already tracked",
                        code=out["code"], note="", eligibility="Added anyway")
            return self.send(200, job_view(job))
        if n == 3 and p[0] == "add-links" and p[2] == "confirm":
            job = JOBS.get(p[1])
            if not job:
                raise ApiError("I can't find that.", 404)
            urls = []
            for item in job["items"]:
                lst = item.get("listing")
                if not lst:
                    continue
                ids = set(body.get("selected") or [])
                for j in lst["jobs"]:
                    if j["id"] in ids and j["link"] and j["link"] not in urls and public_url(j["link"]):
                        urls.append(j["link"])
            if not urls:
                raise ApiError("Pick at least one job.")
            if len(urls) > 15:
                raise ApiError("That's a lot at once. Pick 15 or fewer.")
            return self.send(200, job_view(start_add_links("\n".join(urls), force_urls=urls if body.get("anyway") else ())))
        if n == 3 and p[0] in ("add-links", "chat") and p[2] == "cancel":
            job = JOBS.get(p[1])
            if job:
                job["cancelled"] = True
                A.cancel(job["id"])
                if p[0] == "chat":
                    job["state"] = "cancelled"
            return self.send(200, {"ok": True})
        if p == ["chat"]:
            q_text = C.clean(body.get("text", ""), 800)
            if not q_text:
                raise ApiError("Type a question first.")
            job = new_job("chat")
            threading.Thread(target=run_chat, args=(job, q_text), daemon=True).start()
            return self.send(200, job_view(job))
        if p == ["check-email"]:
            if C.now_ts() - (load_meta().get("checkRequestedAt") or 0) < 60:
                return self.send(200, {"ok": True, "message": "Already checking your email. New replies appear here in a few minutes."})
            if not INTAKE_JOB_ID:
                raise ApiError("The email check isn't set up (set intake_job_id in the config).", 400)
            try:
                rc = subprocess.run([C.hermes_bin(), "cron", "run", INTAKE_JOB_ID], capture_output=True, text=True, timeout=60).returncode
            except (subprocess.SubprocessError, OSError):
                rc = 1
            if rc != 0:
                raise ApiError("I couldn't start the email check.", 500)
            update_meta(lambda m: m.__setitem__("checkRequestedAt", C.now_ts()))
            return self.send(200, {"ok": True, "message": "Checking your email. New replies appear here in a few minutes."})
        if n == 3 and p[0] == "attention" and p[2] in ("dismiss", "snooze"):
            if not re.fullmatch(r"[0-9A-Fa-f]{4,6}:[a-z]{3,12}:[0-9A-Za-z:_.-]{1,60}", p[1]):
                raise ApiError("I can't find that card.", 404)
            try:
                days = max(1, min(60, int(body.get("days") or 3)))
            except (TypeError, ValueError):
                days = 3
            until = "forever" if p[2] == "dismiss" else C.now_ts() + 86400 * days
            update_meta(lambda m: m["dismissed"].__setitem__(p[1], until))
            return self.send(200, {"ok": True})
        if p == ["views"]:
            name = C.one_line(body.get("name"), 40)
            if not name:
                raise ApiError("Give the view a name.")
            f = body.get("filters") if isinstance(body.get("filters"), dict) else {}
            f = json.loads(json.dumps(f))
            if len(json.dumps(f)) > 2000:
                raise ApiError("That view is too big.")

            def add(m):
                m["views"] = [v for v in m["views"] if v["name"] != name][-19:] + [{"name": name, "filters": f}]
            update_meta(add)
            return self.send(200, {"ok": True, "views": load_meta()["views"]})
        if n == 2 and p[0] == "views" and method == "DELETE":
            update_meta(lambda m: m.__setitem__("views", [v for v in m["views"] if v["name"] != p[1]]))
            return self.send(200, {"ok": True, "views": load_meta()["views"]})
        if n == 3 and p[0] == "cards" and p[2] == "resume":
            return self.send(200, start_resume(need_card(p[1]), body))
        if n == 4 and p[0] == "cards" and p[2] == "resume" and p[3] == "read":
            mark_resume_read(need_card(p[1]).code)
            return self.send(200, {"ok": True})
        raise ApiError("Not found.", 404)

    # -- static files
    def static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (STATIC / rel).resolve()
        if not f.is_file() or STATIC.resolve() not in f.parents:
            return self.fail(404, "Not found.")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if f.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/manifest+json", "image/svg+xml"):
            ctype += "; charset=utf-8" if "charset" not in ctype else ""
        data = f.read_bytes()
        self.send(200, data, ctype, {"Cache-Control": "no-cache"})


def start_resume(card, body):
    """Start (or continue) the resume writer for a card through resume_request.py (runs in the background)."""
    script = SCRIPTS / "resume_request.py"
    if not script.exists():
        raise ApiError("The resume writer isn't installed yet.", 501)
    with RESUME_LOCK:
        running = RESUME_CHILDREN.get(card.code)
        own = running is not None and running.poll() is None
        lock_pid = 0 if own else _resume_lock_pid(card.code)
        if body.get("stop"):
            try:
                if own:
                    os.killpg(running.pid, signal.SIGTERM)       # the run is its own session; the script then restores its state
                elif lock_pid:
                    os.kill(lock_pid, signal.SIGTERM)            # started from the CLI or the assistant
            except (ProcessLookupError, PermissionError):
                pass
            return {"ok": True, "resume": resume_state(card.code)}
        if own or lock_pid:
            if any(body.get(k) for k in ("message", "undo", "approve", "feedback", "answers")):
                raise ApiError("I'm still working on the last request. Wait for it to finish, or press Stop.", 409)
            return {"ok": True, "resume": resume_state(card.code)}
        p = _spawn_resume(script, card, body)
        RESUME_CHILDREN[card.code] = p
        CHILD_STARTED[card.code] = time.time()
    threading.Thread(target=p.wait, daemon=True).start()      # collect the exit status, no zombies
    return {"ok": True, "resume": resume_state(card.code)}


def _is_resume_script(pid, code):
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    cmd = cmd.lower()
    return b"resume_request.py" in cmd and code.lower().encode() in cmd


def _spawn_resume(script, card, body):
    args = [sys.executable, str(script), card.code]
    if body.get("approve"):
        args.append("--approve")
    if body.get("undo"):
        args.append("--undo")
    if isinstance(body.get("message"), str) and body["message"].strip():
        args.append("--message=" + C.clean(body["message"], 2000))
    if body.get("feedback"):
        args.append("--feedback=" + C.clean(body["feedback"], 1500))
    if body.get("answers"):
        ans = body["answers"] if isinstance(body["answers"], dict) else {}
        args.append("--answer=" + ";".join(f"{re.sub(r'[^0-9]', '', str(k))}={C.one_line(v, 300).replace(';', ',')}"
                                           for k, v in ans.items() if re.sub(r'[^0-9]', '', str(k))))
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, cwd=str(SCRIPTS))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("BOARD_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("BOARD_PORT", "8765")))
    a = ap.parse_args(argv)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"Applications board on http://{a.host}:{a.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
