"""Run: python3 -I hermes/tests/test_jobs_helpers.py
Uses a temp HOME, a temp sqlite board and a fake `hermes` / `himalaya`; never touches the real board or mailbox."""
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["JOBS_AGENT_CONFIG"] = str(Path(__file__).resolve().parent / "fixtures" / "job-agent.test.json")

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

FAKE_HERMES = r'''#!/usr/bin/env python3
import os, re, sqlite3, sys, time, secrets
a = sys.argv[1:]
assert a[0] == "kanban" and a[1] == "--board" and a[2] == "jobs", a
cmd, rest = a[3], a[4:]
con = sqlite3.connect(os.environ["JOBS_BOARD_DB"])
now = int(time.time())
def flag(name):
    return rest[rest.index(name) + 1] if name in rest else None
if cmd == "create":
    title = rest[0]
    key = flag("--idempotency-key")
    row = con.execute("select id from tasks where idempotency_key=?", (key,)).fetchone()
    if row:
        print("existing", row[0]); sys.exit(0)
    body = sys.stdin.read() if flag("--body-file") == "-" else ""
    tid = "t_" + secrets.token_hex(4)
    con.execute("insert into tasks(id,title,body,status,created_at,idempotency_key) values(?,?,?,?,?,?)", (tid, title, body, "blocked", now, key))
    print("created", tid)
elif cmd == "complete":
    con.execute("update tasks set status='done', completed_at=? where id=?", (now, rest[0]))
elif cmd == "comment":
    author = flag("--author") or "default"
    words = [w for i, w in enumerate(rest[1:], 1) if w != "--author" and rest[i - 1] != "--author"]
    con.execute("insert into task_comments(task_id,author,body,created_at) values(?,?,?,?)", (rest[0], author, " ".join(words), now))
elif cmd == "edit":
    con.execute("update tasks set title=? where id=?", (flag("--title"), rest[0]))
elif cmd == "archive":
    con.execute("update tasks set status='archived' where id=?", (rest[0],))
else:
    sys.exit("unsupported " + cmd)
con.commit()
'''

FAKE_HIMALAYA = r'''#!/usr/bin/env python3
import json, os, sys
d = json.load(open(os.environ["FAKE_MAIL"]))
a = sys.argv[1:]
if a[0] == "--json":
    a = a[1:]
if a[0] == "envelope":
    page = int(a[a.index("--page") + 1]) if "--page" in a else 1
    print(json.dumps({"envelopes": d["envelopes"] if page == 1 else []}))
elif a[0] == "message":
    assert "--seen" not in a, "must not mark as read"
    print("From: x\nSubject: s\n\n" + d["bodies"].get(a[-1], ""))
'''


class BoardActionTests(unittest.TestCase):
    def test_stage_review_note_undo_and_actor(self):
        import jobs_common as BC
        e = Env()
        self.assertIn("| in Inbox", e.run("jobs_add.py", *ADD, "--review"))
        tid = sqlite3.connect(e.db).execute("select id from tasks").fetchone()[0]
        code = tid[-4:].upper()
        os.environ["JOBS_BOARD_DB"] = str(e.db)

        def card():
            return BC.load_cards()[0]

        self.assertEqual((card().review, card().stage), ("pending", "New"))
        self.assertIn("need --actor you", e.run("jobs_mark.py", code, "--stage", "applied"))
        self.assertIn("REVIEW-ACCEPTED", e.run("jobs_mark.py", code, "--actor", "you", "--review", "accepted"))
        self.assertEqual(card().review, "accepted")
        self.assertIn("STAGE-SET", e.run("jobs_mark.py", code, "--actor", "you", "--stage", "interview", "--note", "phone screen"))
        c = card()
        self.assertEqual((c.stage, c.applied, c.tag), ("Interview", True, "APPLIED"))
        self.assertTrue(c.stage_events[-1]["by_you"])
        self.assertEqual(c.stage_events[-1]["from"], "new")
        # a later rejection email beats the earlier board click
        e.run("jobs_mark.py", code, "--response", "rejection", "--note", "no thanks", "--deadline", "2026-12-01")
        c = card()
        self.assertEqual(c.stage, "Rejected")
        self.assertEqual(c.responses[-1]["deadline"], "2026-12-01")
        self.assertFalse(c.responses[-1]["by_you"])
        # undo is just another STAGE comment back
        e.run("jobs_mark.py", code, "--actor", "you", "--stage", "applied", "--note", "undo")
        self.assertEqual(card().stage, "Applied")
        self.assertIn("NOTE-ADDED", e.run("jobs_mark.py", code, "--actor", "you", "--add-note", "call Tuesday"))
        self.assertEqual(card().notes[-1]["text"], "call Tuesday")
        e.run("jobs_mark.py", code, "--actor", "you", "--stage", "parked")
        self.assertEqual(card().stage, "Parked")
        e.run("jobs_mark.py", code, "--actor", "you", "--stage", "new")
        self.assertEqual((card().stage, card().applied), ("New", False))


class Env:
    def __init__(self, mail=None):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "bin").mkdir()
        for name, src in (("hermes", FAKE_HERMES), ("himalaya", FAKE_HIMALAYA)):
            p = t / "bin" / name
            p.write_text(src)
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        self.db = t / "kanban.db"
        con = sqlite3.connect(self.db)
        con.executescript("""create table tasks(id text primary key,title text,body text,status text,created_at integer,
            completed_at integer,idempotency_key text);
            create table task_comments(id integer primary key autoincrement,task_id text,author text,body text,created_at integer);""")
        con.commit()
        con.close()
        (t / "mail.json").write_text(json.dumps(mail or {"envelopes": [], "bodies": {}}))
        self.env = {**os.environ, "HOME": str(t), "JOBS_BOARD_DB": str(self.db), "JOBS_STATE_DIR": str(t / "state"),
                    "JOBS_EXPORT_DIR": str(t / "export"), "HERMES_BIN": str(t / "bin" / "hermes"),
                    "HIMALAYA_BIN": str(t / "bin" / "himalaya"), "FAKE_MAIL": str(t / "mail.json"),
                    "PATH": f"{t / 'bin'}:{os.environ['PATH']}"}
        self.export = t / "export"
        (t / ".hermes" / "scripts").mkdir(parents=True)
        (t / ".hermes" / "scripts" / "job_filters.json").write_text((SCRIPTS / "job_filters.json").read_text())

    def run(self, script, *args):
        p = subprocess.run([sys.executable, "-I", str(SCRIPTS / script), *args], env=self.env, capture_output=True, text=True)
        return (p.stdout + p.stderr).strip()

    def seed(self, tid, title, body="", status="blocked", created=1795000000, key=None, comments=()):
        con = sqlite3.connect(self.db)
        con.execute("insert into tasks(id,title,body,status,created_at,completed_at,idempotency_key) values(?,?,?,?,?,?,?)",
                    (tid, title, body, status, created, created + 100 if status == "done" else None, key))
        for text, ts in comments:
            con.execute("insert into task_comments(task_id,author,body,created_at) values(?,?,?,?)", (tid, "default", text, ts))
        con.commit()
        con.close()


ADD = ["--title", "Junior Software Developer", "--company", "Acme Corp", "--location", "Surrey, BC", "--fit", "72",
       "--reason", "Matches web dev work.", "--source", "discord-link", "--link", "https://acme.example/jobs/12345?utm_source=x"]


class AppliedDerivationTests(unittest.TestCase):
    def test_applied_flag_and_date_follow_the_clicks(self):
        import jobs_common as BC
        e = Env()
        e.run("jobs_add.py", *ADD)
        tid = sqlite3.connect(e.db).execute("select id from tasks").fetchone()[0]
        code = tid[-4:].upper()
        os.environ["JOBS_BOARD_DB"] = str(e.db)
        mark = lambda *a: e.run("jobs_mark.py", code, "--actor", "you", *a)
        card = lambda: BC.load_cards()[0]
        # New -> Rejected with no application is not "applied"
        mark("--stage", "rejected")
        self.assertEqual((card().stage, card().applied), ("Rejected", False))
        # Applied -> Rejected is
        mark("--stage", "applied")
        mark("--stage", "rejected")
        self.assertEqual((card().stage, card().applied), ("Rejected", True))
        # reset and re-apply: the date comes from the new Applied click, and undo clears the flag
        mark("--stage", "new", "--note", "undo")
        self.assertEqual((card().stage, card().applied, card().applied_date), ("New", False, ""))
        mark("--stage", "applied")
        self.assertTrue(card().applied)
        self.assertEqual(card().applied_date, card().stage_events[-1]["date"])
        mark("--stage", "parked")
        self.assertEqual((card().stage, card().applied), ("Parked", False))
        # every activity row carries the event number the undo list needs
        self.assertTrue(all("seq" in a for a in card().activity))


class GateTests(unittest.TestCase):
    def setUp(self):
        import jobs_gate
        self.g = jobs_gate

    def v(self, title, loc="", text=""):
        return self.g.evaluate(title, "", loc, text)

    def test_title_rejects(self):
        for t in ("Director of Engineering", "Staff Software Engineer", "Registered Nurse", "Engineering Manager",
                  "Mechanical Engineer", "Principal Developer"):
            self.assertEqual(self.v(t)["verdict"], "reject", t)
        for t in ("Junior Software Developer", "QA Analyst", "Apprentice Electrician (2027 Spring)", "Sales Associate",
                  "Software Architect", "Teaching Assistant"):
            self.assertNotEqual(self.v(t)["verdict"], "reject", t)

    def test_locations(self):
        for loc in ("Toronto, ON", "Remote - US", "Seattle, WA", "Calgary, Alberta", "Victoria, BC", "London, UK"):
            self.assertEqual(self.v("Dev", loc)["verdict"], "reject", loc)
        for loc in ("Surrey, BC", "Remote - Canada", "Vancouver, BC (Hybrid)", "Remote", "", "Burnaby"):
            self.assertNotEqual(self.v("Dev", loc)["verdict"], "reject", loc)

    def test_degree_rule(self):
        rej = ["Bachelor's degree in Nursing required.", "Master's degree required.", "PhD in Computer Science required.",
               "Diploma in Early Childhood Education required.", "Degree in Marketing or Business Administration required."]
        ok = ["Bachelor's degree in Computer Science, Engineering or a related field.", "Bachelor's degree in any discipline.",
              "A university degree is required.", "Bachelor's degree or equivalent experience.",
              "Bachelor's degree in Marketing preferred.", "High school diploma required.", "No degree required.",
              "Bachelor's in Computer Science or Master's degree in a related field"]
        for t in rej:
            self.assertEqual(self.v("Dev", "Surrey", t)["verdict"], "reject", t)
        for t in ok:
            self.assertEqual(self.v("Dev", "Surrey", t)["verdict"], "eligible", t)

    def test_years_citizenship_us_only(self):
        self.assertEqual(self.v("Dev", "", "10+ years of experience required")["verdict"], "reject")
        r = self.v("Dev", "", "5+ years of experience with Python")
        self.assertEqual(r["verdict"], "eligible")
        self.assertTrue(r["notes"])
        self.assertEqual(self.v("Dev", "", "3+ years experience; 10 years preferred")["verdict"], "eligible")
        self.assertEqual(self.v("Dev", "", "Must be a Canadian citizen.")["verdict"], "reject")
        self.assertEqual(self.v("Dev", "", "Active secret security clearance required")["verdict"], "reject")
        self.assertEqual(self.v("Dev", "", "Must be authorized to work in the United States.")["verdict"], "reject")
        self.assertEqual(self.v("Dev", "", "Open to the US and Canada. Authorized to work in the US or Canada.")["verdict"], "eligible")


class BoardTests(unittest.TestCase):
    def test_codes_urls_dupes(self):
        import jobs_common as C
        e = Env()
        os.environ["JOBS_BOARD_DB"] = str(e.db)
        e.seed("t_58b478f8", "[APPLIED] Dev - Wayne Works", "Company: Wayne Works | Location: Vancouver\nLink: https://x.example/a", "done")
        e.seed("t_aaaa78f8", "[50] Other - Foo", "Company: Foo | Location: Surrey\nLink: https://foo.example/jobs/998877")
        cards = C.load_cards()
        self.assertEqual({c.code for c in cards}, {"B478F8", "AA78F8"})   # both end in 78F8 -> 6-char codes
        self.assertEqual(C.norm_url("https://WWW.Example.com/jobs/1/?utm_source=a&id=7&ref=z#frag"), "https://example.com/jobs/1?id=7")
        self.assertEqual(C.norm_url("https://www.linkedin.com/jobs/view/software-dev-at-acme-3801234567/?trk=x"),
                         "https://www.linkedin.com/jobs/view/3801234567")
        d, why = C.find_duplicate(cards, "https://foo.example/jobs/998877?x=1#a", "Foo", "Different title", "Surrey")
        self.assertIsNotNone(d)
        d, _ = C.find_duplicate(cards, "", "Foo", "Other", "Calgary")      # same title, other location: a different job
        self.assertIsNone(d)
        d, _ = C.find_duplicate(cards, "", "Foo Inc.", "Other", "Surrey, BC")
        self.assertIsNotNone(d)
        del os.environ["JOBS_BOARD_DB"]


class ScriptTests(unittest.TestCase):
    def test_add_dedupe_reject_force(self):
        e = Env()
        out = e.run("jobs_add.py", *ADD)
        self.assertRegex(out, r"^CREATED \[[0-9A-F]{4}\] fit 72: Junior Software Developer - Acme Corp - posted date unknown")
        out2 = e.run("jobs_add.py", *ADD)
        self.assertTrue(out2.startswith("ALREADY-TRACKED"), out2)
        # same requisition under a different URL shape
        out3 = e.run("jobs_add.py", *(ADD[:-1] + ["https://acme.example/jobs/12345/"]), "--posted-ago", "3 days ago")
        self.assertTrue(out3.startswith("ALREADY-TRACKED"), out3)
        rej = ["--title", "Staff Software Engineer", "--company", "Big", "--fit", "30", "--reason", "x", "--link", "https://big.example/j/1"]
        out4 = e.run("jobs_add.py", *rej)
        self.assertTrue(out4.startswith("REJECTED:"), out4)
        self.assertIn("Staff Software Engineer", Path(e.env["JOBS_STATE_DIR"], "job_rejects.jsonl").read_text())
        out5 = e.run("jobs_add.py", *rej, "--force")
        self.assertTrue(out5.startswith("CREATED"), out5)
        out6 = e.run("jobs_add.py", "--title", "Dev", "--company", "Nurse Co", "--fit", "40", "--reason", "x", "--link", "https://n.example/9",
                     "--requirements", "Bachelor's degree in Nursing required.")
        self.assertTrue(out6.startswith("REJECTED: requires a degree/diploma in Nursing"), out6)
        bad = e.run("jobs_add.py", "--title", "T", "--company", "C", "--fit", "150", "--reason", "x")
        self.assertIn("--fit must be", bad)
        # body: received date computed in code, posted unknown stored
        con = sqlite3.connect(e.db)
        body = con.execute("select body from tasks where title like '[72]%'").fetchone()[0]
        self.assertIn("Posted: unknown", body)
        self.assertRegex(body, r"Source: discord-link, received \d{4}-\d{2}-\d{2}")
        self.assertTrue((e.export / "jobs-board.html").exists())

    def test_shared_listing_link_is_not_a_duplicate(self):
        e = Env()
        base = ["--company", "Stark Transit", "--location", "Burnaby, BC", "--fit", "60", "--reason", "x", "--source", "discord-link",
                "--link", "https://jobs.stark-transit.example/search"]
        self.assertTrue(e.run("jobs_add.py", "--title", "Data Analyst", *base).startswith("CREATED"))
        self.assertTrue(e.run("jobs_add.py", "--title", "IT Infrastructure Analyst", *base).startswith("CREATED"))
        self.assertTrue(e.run("jobs_add.py", "--title", "Data Analyst", *base).startswith("ALREADY-TRACKED"))
        self.assertEqual(e.run("jobs_list.py", "--dupes"), "no duplicates found")

    def test_applied_flow_and_responses(self):
        e = Env()
        e.run("jobs_add.py", *ADD)
        code = e.run("jobs_list.py", "--counts")
        self.assertIn("0 applied", code)
        lst = e.run("jobs_list.py")
        cd = lst.split("]")[0].lstrip("[")
        self.assertIn("https://acme.example/jobs/12345", lst)
        self.assertIn("posted date unknown", lst)
        self.assertIn("added ", lst)
        self.assertIn("ERROR", e.run("jobs_mark.py", cd, "--applied"))
        self.assertIn("--note", e.run("jobs_mark.py", cd, "--applied", "--by", "receipt"))
        self.assertTrue(e.run("jobs_mark.py", cd, "--applied", "--by", "receipt", "--note", "receipt 2026-11-20: Thanks for applying").startswith("MARKED-APPLIED"))
        self.assertTrue(e.run("jobs_mark.py", cd, "--applied", "--by", "user").startswith("ALREADY-APPLIED"))
        self.assertIn("1 applied", e.run("jobs_list.py", "--counts"))
        r1 = e.run("jobs_mark.py", cd, "--response", "interview", "--note", "phone screen Friday", "--date", "2026-11-23")
        self.assertTrue(r1.startswith("RESPONSE-RECORDED"), r1)
        self.assertTrue(e.run("jobs_mark.py", cd, "--response", "interview", "--note", "again", "--date", "2026-11-23").startswith("ALREADY-RECORDED"))
        self.assertIn("interview", e.run("jobs_list.py", "--stage", "applied,interview"))
        # an alert for an applied job is never re-listed
        self.assertTrue(e.run("jobs_add.py", *ADD).startswith("ALREADY-APPLIED"))
        # a second receipt for an existing alert converts it instead of duplicating
        out = e.run("jobs_add.py", "--applied", "--company", "Acme Corp", "--title", "Junior Software Developer")
        self.assertTrue(out.startswith("ALREADY-APPLIED"), out)
        e2 = e.run("jobs_add.py", "--applied", "--company", "Zed", "--title", "Tester", "--note", "receipt 2026-11-24")
        self.assertTrue(e2.startswith("CREATED"), e2)
        self.assertIn("2 applied", e.run("jobs_list.py", "--counts"))

    def test_digest_filters(self):
        e = Env()
        e.run("jobs_add.py", *ADD)                                    # discord-link: never in the digest
        e.run("jobs_add.py", "--title", "Support Analyst", "--company", "Beta", "--location", "Remote - Canada", "--fit", "65",
              "--reason", "fits", "--source", "email", "--link", "https://beta.example/j/22")
        e.run("jobs_add.py", "--title", "Low Fit", "--company", "Gamma", "--fit", "20", "--reason", "meh", "--source", "email",
              "--link", "https://gamma.example/j/3")
        out = e.run("jobs_digest.py", "--dry-run")
        self.assertIn("Support Analyst", out)
        self.assertNotIn("Junior Software Developer", out)
        self.assertNotIn("Low Fit", out)
        self.assertNotIn("N applied total", out)
        self.assertIn("0 applied", out)
        e.run("jobs_digest.py")                                        # marks
        self.assertEqual(e.run("jobs_digest.py"), "")                  # nothing new -> silent

    def test_export_is_escaped(self):
        e = Env()
        e.seed("t_11112222", "[50] </script><script>alert(1)</script> - =cmd|x", "Company: =cmd|x\nLink: javascript:alert(1)\nLocation: Surrey")
        e.run("jobs_export.py")
        html = (e.export / "jobs-board.html").read_text()
        self.assertNotIn("<script>alert(1)", html)
        self.assertEqual(html.count("</script>"), 1)
        csv_text = (e.export / "applications.csv").read_text()
        self.assertIn("'=cmd|x", csv_text)

    def test_gate_cli_batch(self):
        e = Env()
        f = Path(e.tmp.name) / "batch.txt"
        f.write_text("Junior Developer | Acme | Surrey, BC | https://a/1\nDirector of IT | Acme | Surrey | https://a/2\nSupport | Acme | Toronto, ON | https://a/3\n")
        out = e.run("jobs_gate.py", "--batch-file", str(f), "--log")
        self.assertIn("SUMMARY: 1 to open, 2 rejected", out)
        self.assertIn("2 rejected", out)
        self.assertEqual(len(Path(e.env["JOBS_STATE_DIR"], "job_rejects.jsonl").read_text().splitlines()), 2)

    def test_review_regressions(self):
        sys.path.insert(0, str(SCRIPTS))
        import jobs_gate as G
        v = lambda t, req: G.evaluate("Dev", "Acme", "Surrey, BC", t, req_text=req)["verdict"]
        self.assertEqual(v("Must be at least 19 years of age", None), "eligible")
        self.assertEqual(v("x", "We are looking for 10+ years of experience"), "reject")
        self.assertEqual(v("Bachelor's degree in Economics", None), "reject")
        self.assertEqual(v("Bachelor's degree in Computer Science", None), "eligible")
        e = Env()
        base = ["--company", "Acme", "--fit", "60", "--reason", "x", "--source", "discord-link", "--link", "https://jobs.acme.example/search"]
        self.assertTrue(e.run("jobs_add.py", "--title", "Developer", "--location", "Vancouver, BC", *base).startswith("CREATED"))
        self.assertTrue(e.run("jobs_add.py", "--title", "Developer", "--location", "North Vancouver, BC", *base).startswith("CREATED"))

    def test_email_match_is_whole_word(self):
        sys.path.insert(0, str(SCRIPTS))
        import job_email_lookup as L
        n, w = L.company_tokens("Meta")
        mk = lambda frm, subj: {"from": [{"name": frm, "addr": "x@example.com"}], "subject": subj}
        self.assertFalse(L.matches_company(mk("Bob", "metadata report"), n, w))
        self.assertTrue(L.matches_company(mk("Meta Careers", "Hello"), n, w))
        n, w = L.company_tokens("Royal Bank")
        self.assertFalse(L.matches_company(mk("Bob", "Bank statement: Royal wedding"), n, w))

    def test_email_lookup_privacy(self):
        mail = {"envelopes": [
            {"id": "5", "from": [{"name": "Globex Inc", "addr": "noreply@globex-careers.example"}], "subject": "Application update", "date": "2099-01-01 10:00:00+00:00"},
            {"id": "4", "from": [{"name": "Mom", "addr": "mom@gmail.com"}], "subject": "dinner secret plans", "date": "2099-01-01 09:00:00+00:00"}],
            "bodies": {"5": "Unfortunately we will not be moving forward with your application.", "4": "SECRET BODY"}}
        e = Env(mail)
        self.assertIn("NOT TRACKED", e.run("job_email_lookup.py", "Globex", "--days", "36500"))
        e.seed("t_0000abcd", "[APPLIED] Implementation Consultant - Globex Inc", "Company: Globex Inc\nLink: https://x/1", "done")
        out = e.run("job_email_lookup.py", "Globex", "--days", "36500")
        self.assertIn("Type-hint: REJECTION", out)
        self.assertIn("noreply@globex-careers.example", out)
        self.assertNotIn("dinner", out)
        self.assertNotIn("SECRET BODY", out)
        none = e.run("job_email_lookup.py", "Nobody Corp", "--days", "36500")
        self.assertIn("NOT TRACKED", none)
        self.assertIn("NOT TRACKED", e.run("job_email_lookup.py", "password", "--days", "36500"))
        sweep = e.run("job_email_lookup.py", "--sweep", "--days", "36500")
        self.assertIn("## Globex Inc", sweep)
        self.assertNotIn("SECRET BODY", sweep)


if __name__ == "__main__":
    unittest.main(verbosity=1)
