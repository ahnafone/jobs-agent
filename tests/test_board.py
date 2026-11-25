"""Run: python3 -I hermes/tests/test_board.py
Starts the real board server on a free port against a temp sqlite board with a fake `hermes` (kanban, chat and
cron). Never touches the real board, mailbox or model."""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

os.environ["JOBS_AGENT_CONFIG"] = str(Path(__file__).resolve().parent / "fixtures" / "job-agent.test.json")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_jobs_helpers import Env, ADD, SCRIPTS  # noqa: E402

BOARD = HERE.parent / "board"
PASSWORD = "correct horse battery"

FAKE_WRAPPER = r'''#!/usr/bin/env python3
import json, os, re, subprocess, sys
a = sys.argv[1:]
log = os.environ["FAKE_LOG"]
if a and a[0] == "cron":
    open(log, "a").write("cron " + " ".join(a[1:]) + "\n")
    sys.exit(0)
if a and a[0] == "chat":
    qf = a[a.index("--query-file") + 1]
    prompt = open(qf).read()
    open(log, "a").write("chat model=" + a[a.index("-m") + 1] + " toolsets=" + a[a.index("-t") + 1] + "\n")
    m = re.search(r"<url>(.*?)</url>", prompt)
    if m:
        url = m.group(1)
        if "listing" in url:
            out = {"kind": "listing", "source": "Acme Careers", "openings": [
                {"title": "QA Analyst", "team": "Quality", "location": "Surrey, BC", "link": "https://acme.example/jobs/1001"},
                {"title": "Staff Software Engineer", "team": "Core", "location": "Remote", "link": "https://acme.example/jobs/1002"}]}
        elif "broken" in url:
            out = {"kind": "error", "reason": "login wall"}
        elif "staff" in url:
            out = {"kind": "job", "title": "Staff Software Engineer", "company": "Big", "location": "Remote", "fit": 30,
                   "requirements": "10+ years of experience", "reason": "too senior"}
        else:
            out = {"kind": "job", "title": "QA Analyst", "company": "Acme", "location": "Surrey, BC", "fit": 71,
                   "pay": "CAD 70,000", "posted": "2026-11-20", "requirements": "Diploma or degree in a related field",
                   "summary": "Test software.", "description": "Test the product. Write reports.", "reason": "Good match."}
        print("```json\n" + json.dumps(out) + "\n```")
    else:
        codes = re.findall(r"\[([0-9A-F]{4})\]", prompt.split("=== BOARD DATA")[1]) if "=== BOARD DATA" in prompt else []
        print("Here you go " + (f"[{codes[0]}]" if codes else "nothing") + ".\n\nsession_id: abc")
    sys.exit(0)
os.execv(sys.executable, [sys.executable, os.environ["REAL_FAKE_HERMES"], *a])
'''


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class BoardServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        t = Path(cls.e.tmp.name)
        real = t / "bin" / "hermes-kanban.py"
        (t / "bin" / "hermes").rename(real)
        wrapper = t / "bin" / "hermes"
        wrapper.write_text(FAKE_WRAPPER)
        wrapper.chmod(0o755)
        (t / "static").mkdir()
        (t / "static" / "index.html").write_text("<!doctype html><title>Board</title>")
        (t / "static" / "app.js").write_text("console.log(1)")
        cls.log = t / "fake.log"
        cls.log.write_text("")
        env = {**cls.e.env, "REAL_FAKE_HERMES": str(real), "FAKE_LOG": str(cls.log), "BOARD_STATIC_DIR": str(t / "static"),
               "JOBS_SCRIPTS_DIR": str(SCRIPTS), "BOARD_ALLOW_UNRESOLVED": "1",
               "BOARD_AUTH_FILE": str(t / "auth" / "board-auth.json")}
        cls.e.env = env
        subprocess.run([sys.executable, "-I", "-c",
                        f"import sys; sys.path.insert(0, {str(BOARD)!r}); import board_auth as A; A.set_password({PASSWORD!r})"],
                       env=env, check=True)
        cls.port = free_port()
        cls.proc = subprocess.Popen([sys.executable, "-I", str(BOARD / "jobs_board.py"), "--port", str(cls.port)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=str(t))
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        os.environ["JOBS_BOARD_DB"] = str(cls.e.db)
        cls.cookie = cls.sign_in()

    @classmethod
    def sign_in(cls, ip="198.51.100.7", host=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{cls.port}/api/login", data=json.dumps({"password": PASSWORD}).encode(), method="POST",
            headers={"Host": host or f"127.0.0.1:{cls.port}", "X-Board": "1", "Content-Type": "application/json",
                     "CF-Connecting-IP": ip})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.headers["Set-Cookie"].split(";")[0]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.e.tmp.cleanup()

    # -- helpers
    def call(self, method, path, body=None, headers=None, host=None, auth=True):
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if auth:
            h["Cookie"] = self.cookie
        data = None
        if method != "GET":
            h["X-Board"] = "1"
            h["Content-Type"] = "application/json"
            data = json.dumps(body or {}).encode()
        h.update(headers or {})
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                ctype = r.headers.get("Content-Type", "")
                return r.status, (json.loads(raw) if "json" in ctype else raw.decode()), r.headers
        except urllib.error.HTTPError as ex:
            raw = ex.read()
            try:
                return ex.code, json.loads(raw), ex.headers
            except ValueError:
                return ex.code, raw.decode(), ex.headers

    def state(self):
        return self.call("GET", "/api/state")[1]

    def wait_job(self, path, tries=60):
        for _ in range(tries):
            _, j, _ = self.call("GET", path)
            if j.get("state") != "working":
                return j
            time.sleep(0.2)
        self.fail("job did not finish")

    # -- tests (run in name order; each builds on the board the earlier ones leave)
    def test_00_sign_in(self):
        # nothing under /api works without a session, whatever the method; the page itself still loads
        for m, path in [("GET", "/api/state"), ("GET", "/api/activity"), ("GET", "/api/export.csv"), ("GET", "/api/deadlines.ics"),
                        ("GET", "/api/cards/ZZZZ/files/resume"), ("POST", "/api/chat"), ("POST", "/api/undo"), ("GET", "/api/nope")]:
            s, j, _ = self.call(m, path, auth=False)
            self.assertEqual((m, path, s), (m, path, 401))
            self.assertTrue(j["login"])
        self.assertEqual(self.call("GET", "/", auth=False)[0], 200)
        self.assertEqual(self.call("GET", "/api/session", auth=False)[1], {"authed": False})
        self.assertEqual(self.call("GET", "/api/session")[1], {"authed": True})
        # odd spellings of /api/... all still need the session (or are plain 404s with no data)
        for path in ["//api/state", "/%61pi/state", "/api/", "/api/state?x=1", "/./api/state", "/api/%73tate", "/api/state/"]:
            s, j, _ = self.call("GET", path, auth=False)
            self.assertIn(s, (401, 404), path)
            self.assertNotIn("cards", j if isinstance(j, dict) else {}, path)
        for path in ["/API/state", "/api;x/state", "/static/../api/state"]:
            self.assertIn(self.call("GET", path, auth=False)[0], (401, 404), path)
        self.assertEqual(self.call("HEAD", "/api/state", auth=False)[0], 401)
        for m in ("PUT", "PATCH", "OPTIONS"):
            self.assertNotEqual(self.call(m, "/api/state", auth=False)[0], 200, m)
        # a made-up or tampered cookie is no good
        self.assertEqual(self.call("GET", "/api/state", headers={"Cookie": "board=not-a-session"})[0], 401)
        self.assertEqual(self.call("GET", "/api/state", headers={"Cookie": self.cookie + "x"})[0], 401)
        # sign-in needs the same-site headers as every other change
        self.assertEqual(self.call("POST", "/api/login", {"password": PASSWORD}, headers={"X-Board": "0"}, auth=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/login", {"password": PASSWORD}, host="evil.example", auth=False)[0], 421)
        # wrong passwords: the same message whatever is wrong, and a lock after five from one address
        ip = {"CF-Connecting-IP": "203.0.113.9"}
        for i in range(5):
            s, j, _ = self.call("POST", "/api/login", {"password": "nope-%d" % i}, headers=ip, auth=False)
            self.assertEqual((s, j["error"]), (401, "That password isn't right."))
        s, j, h = self.call("POST", "/api/login", {"password": PASSWORD}, headers=ip, auth=False)
        self.assertEqual(s, 429, j)
        self.assertTrue(0 < int(h["Retry-After"]) <= 60)
        self.assertIn("minute", j["error"])
        # ... which doesn't stop someone else, or an already signed-in browser
        self.assertEqual(self.call("POST", "/api/login", {"password": "wrong"}, headers={"CF-Connecting-IP": "203.0.113.10"},
                                   auth=False)[0], 401)
        self.assertEqual(self.call("GET", "/api/state")[0], 200)
        # a bad or oversize body is an error, not a crash
        self.assertEqual(self.call("POST", "/api/login", {"password": 5}, headers={"CF-Connecting-IP": "203.0.113.11"}, auth=False)[0], 401)
        self.assertEqual(self.call("POST", "/api/login", {"password": "x" * 5000}, headers={"CF-Connecting-IP": "203.0.113.12"},
                                   auth=False)[0], 401)

    def test_00_cookie_and_sign_out(self):
        # on the public address the cookie is Secure and host-locked; on localhost it still works over plain http
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/login", data=json.dumps({"password": PASSWORD}).encode(), method="POST",
            headers={"Host": "board.example.com", "X-Board": "1", "Content-Type": "application/json",
                     "CF-Connecting-IP": "198.51.100.20"})
        with urllib.request.urlopen(req, timeout=20) as r:
            c = r.headers["Set-Cookie"]
        for want in ("__Host-board=", "HttpOnly", "SameSite=Strict", "Secure", "Path=/", "Max-Age=2592000"):
            self.assertIn(want, c)
        pub = c.split(";")[0]
        self.assertEqual(self.call("GET", "/api/state", host="board.example.com", headers={"Cookie": pub})[0], 200)
        # a localhost session doesn't work on the public name (different cookie name), and the reverse
        self.assertEqual(self.call("GET", "/api/state", host="board.example.com")[0], 401)
        self.assertEqual(self.call("GET", "/api/state", headers={"Cookie": pub})[0], 401)
        # signing out kills that session on the server, even if the cookie is replayed
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/logout", data=b"{}", method="POST",
                                     headers={"Host": "board.example.com", "X-Board": "1", "Cookie": pub,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            self.assertIn("Max-Age=0", r.headers["Set-Cookie"])
        self.assertEqual(self.call("GET", "/api/state", host="board.example.com", headers={"Cookie": pub})[0], 401)
        self.assertEqual(self.call("GET", "/api/state")[0], 200)          # the other session is untouched

    def test_99_global_limit(self):
        # 30 wrong tries in an hour, from any addresses, pause new sign-ins for everyone (but not signed-in browsers)
        for i in range(30):
            self.call("POST", "/api/login", {"password": "wrong"}, headers={"CF-Connecting-IP": "192.0.2.%d" % (i + 1)}, auth=False)
        s, j, h = self.call("POST", "/api/login", {"password": PASSWORD}, headers={"CF-Connecting-IP": "192.0.2.200"}, auth=False)
        self.assertEqual(s, 429, j)
        self.assertGreater(int(h["Retry-After"]), 60)
        self.assertEqual(self.call("GET", "/api/state")[0], 200)

    def test_00_cookie_file_is_private(self):
        t = Path(self.e.tmp.name) / "auth"
        for f in ("board-auth.json", "board-sessions.json"):
            self.assertEqual(oct((t / f).stat().st_mode & 0o777), "0o600", f)
        sessions = (t / "board-sessions.json").read_text()
        self.assertNotIn(self.cookie.split("=", 1)[1], sessions)           # only a hash of the token is kept
        self.assertNotIn(PASSWORD, (t / "board-auth.json").read_text())

    def test_01_security(self):
        self.assertEqual(self.call("GET", "/api/state", host="evil.example")[0], 421)
        s, j, _ = self.call("POST", "/api/chat", {"text": "hi"}, headers={"X-Board": "0"})
        self.assertEqual(s, 403)
        s, _, _ = self.call("POST", "/api/chat", {"text": "hi"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(s, 403)
        self.assertEqual(self.call("GET", "/..%2f..%2fetc/passwd")[0], 404)
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        s, body, h = self.call("GET", "/")
        self.assertEqual(s, 200)
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["Cache-Control"], "no-cache")
        self.assertEqual(self.call("GET", "/api/state")[2]["Cache-Control"], "no-store")

    def test_02_inbox_accept_stage_undo_note_activity(self):
        self.e.run("jobs_add.py", *ADD, "--review")
        st = self.state()
        self.assertEqual((len(st["cards"]), len(st["inbox"])), (0, 1))
        code = st["inbox"][0]["code"]
        s, j, _ = self.call("POST", f"/api/inbox/{code}/accept")
        self.assertTrue(j["ok"], j)
        st = self.state()
        self.assertEqual((len(st["cards"]), len(st["inbox"])), (1, 0))
        self.assertEqual(st["cards"][0]["stage"], "new")
        s, j, _ = self.call("POST", f"/api/cards/{code}/stage", {"stage": "applied"})
        self.assertEqual(j["undo"]["to"], "new")
        c = self.state()["cards"][0]
        self.assertEqual(c["stage"], "applied")
        self.assertTrue(c["appliedOn"])
        self.assertTrue(any(t["by"] == "you" for t in c["timeline"]))
        # sending an applied card back to the Inbox is refused
        self.assertEqual(self.call("POST", "/api/undo", {"code": code, "review": "pending"})[0], 400)
        self.call("POST", f"/api/cards/{code}/note", {"text": "Call on Tuesday"})
        self.assertEqual(self.state()["cards"][0]["notes"][0]["text"], "Call on Tuesday")
        items = self.call("GET", "/api/activity")[1]["items"]
        undo = next(i["undo"] for i in items if i["undo"])
        self.assertEqual(undo, {"code": code, "to": "new"})
        self.call("POST", "/api/undo", undo)
        self.assertEqual(self.state()["cards"][0]["stage"], "new")
        self.assertEqual(self.call("POST", "/api/cards/ZZZZ/stage", {"stage": "applied"})[0], 404)
        self.assertEqual(self.call("POST", f"/api/cards/{code}/stage", {"stage": "bogus"})[0], 400)
        # decline path
        self.e.run("jobs_add.py", "--title", "Dev Two", "--company", "Beta", "--location", "Surrey, BC", "--fit", "60",
                   "--reason", "ok", "--source", "email", "--link", "https://beta.example/jobs/2", "--review")
        c2 = self.state()["inbox"][0]["code"]
        self.call("POST", f"/api/inbox/{c2}/decline", {"reason": "not interested"})
        st = self.state()
        self.assertEqual(len(st["inbox"]), 0)
        self.assertEqual(self.call("POST", f"/api/inbox/{c2}/decline")[0], 400)

    def test_03_add_links_single_listing_error_and_ineligible(self):
        _, j, _ = self.call("POST", "/api/add-links", {"text": "https://jobs.example/qa-analyst/55 https://jobs.example/broken"})
        self.assertEqual(len(j["items"]), 2)
        done = self.wait_job(f"/api/add-links/{j['id']}")
        first, second = done["items"]
        self.assertEqual(first["verdict"], "added", first)
        self.assertEqual(first["item"]["title"], "QA Analyst")
        self.assertEqual(second["verdict"], "error")
        # the same link again is already tracked
        _, j2, _ = self.call("POST", "/api/add-links", {"text": "https://jobs.example/qa-analyst/55"})
        self.assertIn(self.wait_job(f"/api/add-links/{j2['id']}")["items"][0]["verdict"], ("tracked", "added"))
        # ineligible single job -> then "add anyway"
        _, j3, _ = self.call("POST", "/api/add-links", {"text": "https://jobs.example/staff-role"})
        d3 = self.wait_job(f"/api/add-links/{j3['id']}")
        self.assertEqual(d3["items"][0]["verdict"], "ineligible", d3)
        self.assertEqual(len(self.state()["ineligible"]), 1)
        _, d3b, _ = self.call("POST", f"/api/add-links/{j3['id']}/anyway", {"n": 0})
        self.assertEqual(d3b["items"][0]["verdict"], "added")
        self.assertEqual(len(self.state()["ineligible"]), 0)
        # listing page -> review -> confirm
        _, j4, _ = self.call("POST", "/api/add-links", {"text": "https://acme.example/listing"})
        d4 = self.wait_job(f"/api/add-links/{j4['id']}")
        lst = d4["items"][0]["listing"]
        self.assertEqual(lst["found"], 2)
        self.assertTrue(lst["jobs"][0]["eligible"])
        self.assertFalse(lst["jobs"][1]["eligible"])
        s, j5, _ = self.call("POST", f"/api/add-links/{j4['id']}/confirm", {"selected": [lst["jobs"][0]["id"]]})
        self.assertEqual(s, 200)
        self.assertEqual(self.call("POST", f"/api/add-links/{j4['id']}/confirm", {"selected": []})[0], 400)
        # bad links
        self.assertEqual(self.call("POST", "/api/add-links", {"text": "no links here"})[0], 400)
        self.assertEqual(self.call("POST", "/api/add-links", {"text": "http://127.0.0.1:8642/x"})[0], 400)
        self.assertEqual(self.call("POST", "/api/add-links", {"text": "http://169.254.169.254/latest"})[0], 400)
        self.assertEqual(self.call("POST", "/api/add-links", {"text": "http://localhost/x"})[0], 400)
        log = self.log.read_text()
        self.assertIn("chat model=deepseek-v4.1-flash toolsets=web,browser", log)

    def test_04_chat(self):
        code = self.state()["cards"][0]["code"]
        _, j, _ = self.call("POST", "/api/chat", {"text": "what is new?"})
        d = self.wait_job(f"/api/chat/{j['id']}")
        self.assertEqual(d["state"], "done", d)
        self.assertNotIn("session_id", d["text"])
        self.assertTrue(d["cards"] == [] or d["cards"][0] in {c["code"] for c in self.state()["cards"]})
        self.assertEqual(len(self.call("GET", "/api/chat/history")[1]["items"]), 2)
        self.assertEqual(self.call("POST", "/api/chat", {"text": "  "})[0], 400)
        self.assertIn("toolsets=todo", self.log.read_text().split("chat model=")[-1])   # no web without a pasted link
        # a pasted public link turns web on, and then the model gets no board data
        _, j2, _ = self.call("POST", "/api/chat", {"text": "read https://careers.example/job/9 for me"})
        d2 = self.wait_job(f"/api/chat/{j2['id']}")
        self.assertEqual(d2["state"], "done", d2)
        self.assertIn("toolsets=web,browser", self.log.read_text().split("chat model=")[-1])

    def test_05_calendar_csv_handover_views_attention(self):
        code = self.state()["cards"][0]["code"]
        self.e.run("jobs_mark.py", code, "--response", "assessment", "--note", "Take the online test", "--deadline", "2099-01-05")
        st = self.state()
        self.assertTrue(any(a["kind"] == "deadline" and a["code"] == code for a in st["attention"]))
        s, ics, h = self.call("GET", "/api/deadlines.ics")
        self.assertIn("DTSTART;VALUE=DATE:20990105", ics)
        self.assertIn("text/calendar", h["Content-Type"])
        self.assertIn("BEGIN:VEVENT", self.call("GET", f"/api/cards/{code}/calendar.ics")[1])
        s, csv, h = self.call("GET", "/api/export.csv")
        self.assertIn("text/csv", h["Content-Type"])
        self.assertIn(code, csv)
        _, ho, _ = self.call("GET", f"/api/cards/{code}/handover")
        self.assertIn("I want to apply to this job", ho["prompt"])
        att = next(a for a in st["attention"] if a["kind"] == "deadline")
        self.call("POST", f"/api/attention/{att['id']}/dismiss")
        self.assertFalse(any(a["id"] == att["id"] for a in self.state()["attention"]))
        _, v, _ = self.call("POST", "/api/views", {"name": "Remote only", "filters": {"work": "Remote"}})
        self.assertEqual(v["views"][0]["name"], "Remote only")
        _, v, _ = self.call("DELETE", "/api/views/Remote%20only")
        self.assertEqual(v["views"], [])

    def test_06_check_email_merge_resume(self):
        s, j, _ = self.call("POST", "/api/check-email")
        self.assertTrue(j["ok"])
        self.assertIn("cron run abc123def456", self.log.read_text())
        self.assertGreater(self.state()["checkRequestedAt"], 0)
        cards = self.state()["cards"]
        self.assertGreaterEqual(len(cards), 2)
        keep, drop = cards[0]["code"], cards[1]["code"]
        n = len(cards)
        self.call("POST", "/api/merge", {"keep": keep, "drop": drop})
        self.assertEqual(len(self.state()["cards"]), n - 1)
        self.assertEqual(self.call("POST", "/api/merge", {"keep": keep, "drop": keep})[0], 400)
        self.assertEqual(self.call("GET", f"/api/cards/{keep}/resume")[1], {"resume": None})

    def test_07_resume_files(self):
        code = self.state()["cards"][0]["code"]
        t = Path(self.e.tmp.name)
        pdf = t / "export" / "Resumes" / "Resume-Test.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF-1.4 test")
        secret = t / "secret.txt"
        secret.write_text("nope")
        rd = t / "state" / "resume_requests"
        rd.mkdir(parents=True, exist_ok=True)
        (rd / f"{code}.json").write_text(json.dumps({"status": "ready", "step": "waiting for approval", "questions": [],
                                                    "files": {"resume_pdf": str(pdf), "letter_pdf": str(secret)}}))
        _, r, _ = self.call("GET", f"/api/cards/{code}/resume")
        self.assertEqual(r["resume"]["files"]["resume_pdf"], "Resume-Test.pdf")     # names only, never local paths
        s, body, h = self.call("GET", f"/api/cards/{code}/files/resume_pdf")
        self.assertEqual((s, h["Content-Type"]), (200, "application/pdf"))
        self.assertIn("frame-ancestors 'self'", h["Content-Security-Policy"])
        self.assertEqual(self.call("GET", f"/api/cards/{code}/files/letter_pdf")[0], 404)     # outside Resumes / Cover Letters
        self.assertEqual(self.call("GET", f"/api/cards/{code}/files/../../x")[0], 404)
        self.assertEqual(self.call("GET", f"/api/cards/{code}/files/resume_docx")[0], 404)
        # a run that died (no live lock, old timestamp) is reported as failed, not "working" forever
        (rd / f"{code}.json").write_text(json.dumps({"status": "writing_resume", "updated_at": "2020-01-01T00:00:00+00:00"}))
        r = self.call("GET", f"/api/cards/{code}/resume")[1]["resume"]
        self.assertEqual(r["status"], "failed")
        self.assertIn("interrupted", r["error"])

    def test_08_messages_chat_read_and_message_post(self):
        code = self.state()["cards"][0]["code"]
        t = Path(self.e.tmp.name)
        rd = t / "state" / "resume_requests"
        pdf = t / "export" / "Resumes" / "Resume-Test.pdf"
        for old in rd.glob(f"{code}.*"):
            old.unlink()
        (rd / f"{code}.json").write_text(json.dumps({
            "code": code, "status": "ready", "step": "waiting for approval", "questions": [], "version": 2,
            "versions": [{"n": 1, "kind": "draft", "base": 0}, {"n": 2, "kind": "revision", "base": 1}],
            "files": {"resume_pdf": str(pdf)}, "updated_at": "2026-01-01T00:00:00+00:00"}))
        (rd / f"{code}.chat.json").write_text(json.dumps({"messages": [
            {"role": "agent", "text": "Here they are (v1).", "ts": "2026-01-01T00:00:01.000+00:00", "version": 1,
             "path": "/home/secret"},
            {"role": "user", "text": "shorter please", "ts": "2026-01-01T00:00:02.000+00:00"},
            {"role": "agent", "text": "Done, v2.", "ts": "2026-01-01T00:00:03.000+00:00", "version": 2},
            {"role": "agent", "text": "Approved.", "ts": "2026-01-01T00:00:04.000+00:00", "quiet": True}]}))
        _, m, _ = self.call("GET", "/api/messages")
        row = next(x for x in m["threads"] if x["code"] == code)
        self.assertEqual((row["unread"], row["status"], row["version"], row["from"]), (2, "ready", 2, "agent"))
        self.assertEqual(m["unread"], 2)
        self.assertEqual(self.state()["messagesUnread"], 2)
        # the chat: only the fields the screen needs, never local paths; undo is offered for a revision
        s, c, _ = self.call("GET", f"/api/cards/{code}/resume/chat")
        self.assertEqual(s, 200)
        self.assertEqual([x["role"] for x in c["messages"]], ["agent", "user", "agent", "agent"])
        self.assertTrue(all(set(x) <= {"role", "text", "ts", "version", "quiet"} for x in c["messages"]))
        self.assertNotIn("/home/", json.dumps(c))
        self.assertEqual((c["resume"]["canUndo"], c["resume"]["undoTo"], c["resume"]["version"]), (True, 1, 2))
        # marking read clears the badge; the same-site rules apply to it like any other change
        self.assertEqual(self.call("POST", f"/api/cards/{code}/resume/read", headers={"X-Board": "0"})[0], 403)
        self.assertEqual(self.call("POST", f"/api/cards/{code}/resume/read", host="evil.example")[0], 421)
        self.assertEqual(self.call("POST", f"/api/cards/{code}/resume/read", auth=False)[0], 401)
        self.assertEqual(self.call("GET", "/api/messages", auth=False)[0], 401)
        self.assertEqual(self.call("GET", f"/api/cards/{code}/resume/chat", auth=False)[0], 401)
        self.assertEqual(self.call("GET", "/api/cards/ZZZZ/resume/chat")[0], 404)
        self.assertEqual(self.call("POST", f"/api/cards/{code}/resume/read")[0], 200)
        self.assertEqual(next(x for x in self.call("GET", "/api/messages")[1]["threads"] if x["code"] == code)["unread"], 0)
        self.assertEqual(self.state()["messagesUnread"], 0)
        # stop with nothing running is harmless
        self.assertEqual(self.call("POST", f"/api/cards/{code}/resume", {"stop": True})[0], 200)
        # a chat message runs the writer script; here the fake writer fails, so the last good files stay on offer
        (t / "export" / "profile.md").write_text("# Profile\n- Software developer.\n")
        (t / "export" / "odd-jobs-profile.md").write_text("# Odd jobs\n- Sales Associate.\n")
        s, j, _ = self.call("POST", f"/api/cards/{code}/resume", {"message": "make it punchier"})
        self.assertEqual(s, 200)
        for _ in range(100):
            texts = [x["text"] for x in self.call("GET", f"/api/cards/{code}/resume/chat")[1]["messages"]]
            if any("couldn't make that change" in x for x in texts):
                break
            time.sleep(0.2)
        else:
            self.fail(f"the message was never handled: {texts}")
        c = self.call("GET", f"/api/cards/{code}/resume/chat")[1]
        self.assertIn("make it punchier", [x["text"] for x in c["messages"] if x["role"] == "user"])
        self.assertEqual((c["resume"]["status"], c["resume"]["version"]), ("ready", 2))
        self.assertEqual(next(x for x in self.call("GET", "/api/messages")[1]["threads"] if x["code"] == code)["unread"], 1)

    def test_09_busy_stop_and_no_paths_in_the_browser(self):
        code = self.state()["cards"][0]["code"]
        rd = Path(self.e.tmp.name) / "state" / "resume_requests"
        for old in rd.glob(f"{code}.*"):
            old.unlink()
        (rd / f"{code}.json").write_text(json.dumps({
            "code": code, "status": "failed", "step": "gave up", "questions": [], "files": {},
            "error": "missing /home/user/Documents/Job Applications/profile.md", "errors": ["could not read ~/.hermes/x/y.json"],
            "updated_at": "2026-01-01T00:00:00+00:00"}))
        (rd / f"{code}.chat.json").write_text(json.dumps({"messages": [
            {"role": "agent", "text": f"I couldn't finish. missing {self.e.export}/Resumes/My Resume.pdf and/or more, see https://x.example/a/b", "ts": "2026-01-01T00:00:01.000+00:00"}]}))
        c = self.call("GET", f"/api/cards/{code}/resume/chat")[1]
        blob = json.dumps(c) + json.dumps(self.call("GET", "/api/messages")[1])
        self.assertNotIn("/home/", blob)
        self.assertNotIn(".hermes/x", blob)
        self.assertNotIn("My Resume", blob)
        self.assertIn("and/or more", blob)
        self.assertIn("https://x.example/a/b", blob)                       # ordinary slashes are left alone
        # a run started somewhere else (its lock names a live resume_request.py): the board sees it, refuses new work, and Stop reaches it
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "resume_request.py", code])
        try:
            (rd / f"{code}.lock").write_text(str(sleeper.pid))
            s, j, _ = self.call("POST", f"/api/cards/{code}/resume", {"message": "shorter"})
            self.assertEqual(s, 409)
            self.assertEqual(self.call("POST", f"/api/cards/{code}/resume", {"approve": True})[0], 409)
            self.assertEqual(self.call("POST", f"/api/cards/{code}/resume", {})[0], 200)       # a plain start is harmless
            self.assertIsNone(sleeper.poll())
            self.assertEqual(self.call("POST", f"/api/cards/{code}/resume", {"stop": True})[0], 200)
            sleeper.wait(10)
        finally:
            if sleeper.poll() is None:
                sleeper.kill()
            (rd / f"{code}.lock").unlink(missing_ok=True)


class PublicUrlTests(unittest.TestCase):
    """The link guard: literal IPs in odd spellings and names that resolve to private addresses are refused."""

    @classmethod
    def setUpClass(cls):
        os.environ["BOARD_ALLOW_UNRESOLVED"] = "1"
        sys.path.insert(0, str(BOARD))
        import jobs_board
        cls.B = jobs_board

    def test_literal_and_encoded_internal_addresses_are_refused(self):
        bad = ["http://127.0.0.1/", "http://127.1/", "http://0x7f.0.0.1/", "http://0177.0.0.1/", "http://2130706433/",
               "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://10.1.2.3/", "http://192.168.0.5/", "http://172.16.0.1/",
               "http://169.254.169.254/latest", "http://100.64.0.1/", "http://0.0.0.0/", "http://localhost/",
               "http://printer.local/", "http://user:pw@example.com/", "ftp://example.com/", "file:///etc/passwd",
               "http://example.com:99999/"]
        for u in bad:
            self.assertFalse(self.B.public_url(u), u)

    def test_public_addresses_and_dns_results(self):
        self.assertTrue(self.B.public_url("http://8.8.8.8/"))
        import socket
        real = socket.getaddrinfo

        def fake(host, *a, **k):
            ip = {"evil.example": "127.0.0.1", "mixed.example": None, "good.example": "93.184.216.34"}.get(host)
            if host == "mixed.example":
                return [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("10.0.0.1", 0))]
            if ip is None:
                return real(host, *a, **k)
            return [(2, 1, 6, "", (ip, 0))]
        socket.getaddrinfo = fake
        try:
            self.assertFalse(self.B.public_url("https://evil.example/job"))     # resolves to loopback
            self.assertFalse(self.B.public_url("https://mixed.example/job"))    # any private record refuses
            self.assertTrue(self.B.public_url("https://good.example/job"))
        finally:
            socket.getaddrinfo = real


class ChatToolsTests(unittest.TestCase):
    def test_chat_has_no_web_tools_unless_the_user_pasted_a_public_link(self):
        sys.path.insert(0, str(BOARD))
        import board_agent as A
        self.assertEqual(A.NO_TOOLS, "todo")
        self.assertIn("no web access", A.chat_prompt([], "ctx", "what is new?", web=False))
        self.assertIn("ONLY that link", A.chat_prompt([], "ctx", "read https://x.example/j", web=True))


if __name__ == "__main__":
    unittest.main(verbosity=1)
