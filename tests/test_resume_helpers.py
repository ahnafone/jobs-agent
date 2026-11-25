"""Run: ~/.hermes/venvs/resume-writer/bin/python -I tests/test_resume_helpers.py
(needs python-docx, so use the resume-writer venv, or `pip install python-docx`; also LibreOffice and poppler).
Uses a temp HOME, a temp sqlite board, a fake `hermes` that returns canned writer replies, a temp export folder and
synthetic Word templates built by templates_fixture.py. Settings come from fixtures/job-agent.test.json.
"""
import copy
import json
import re
import os
import shutil
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import resume_render as rr  # noqa: E402  (python-docx needed; see the run line above)
import templates_fixture  # noqa: E402

_TPL = tempfile.TemporaryDirectory()
TEMPLATES = templates_fixture.build(_TPL.name)

FAKE_HERMES = r'''#!/usr/bin/env python3
import json, os, sys, time
a = sys.argv[1:]
qf = a[a.index("--query-file") + 1]
with open(os.environ["FAKE_WRITER_CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": a, "prompt": open(qf, encoding="utf-8").read()}) + "\n")
time.sleep(float(os.environ.get("FAKE_WRITER_SLEEP", "0")))
queue = json.load(open(os.environ["FAKE_WRITER_QUEUE"], encoding="utf-8"))
if not queue:
    sys.exit(9)
item = queue.pop(0)
json.dump(queue, open(os.environ["FAKE_WRITER_QUEUE"], "w", encoding="utf-8"))
sys.stdout.write(item)
'''

KEYWORDS = ["React", "Node.js", "PostgreSQL", "Docker", "Agile", "Git", "TypeScript", "Express.js",
            "Firebase", "Laravel", "code review", "SQL"]
LETTER = [
    "I am applying for the Full Stack Developer role at Example Corp. I am a software developer with 5+ years of "
    "experience building web applications with React, Node.js and PostgreSQL, and a BSc in Computer Science from "
    "Example University.",
    "My work covers the whole stack. At Acme Robotics I built Taskboard, an internal task system on Laravel, PHP and "
    "MySQL. I tuned its SQL queries so the task board loads in under 500 ms, and automated reminders that cut "
    "missed clock-outs by 90%.",
    "At Example Studio I rebuilt a legacy WordPress site as an Angular and Firebase app and maintain it with "
    "regular releases. Before that I worked on the BookingHub booking platform for Globex Corp, which gave me "
    "experience with secure accounts and service logic.",
    "I run Example Studio and most of my work has come from contract projects, so I am used to taking a project "
    "from the first client meeting through to long-term support and maintenance. I would welcome the chance to "
    "discuss how I can contribute to your team.",
]


def software_spec(**over):
    s = {
        "job": {"company": "Example Corp", "title": "Full Stack Developer", "template": "software"},
        "keywords": list(KEYWORDS),
        "resume": {
            "summary": "Full Stack Developer with 5+ years of experience building web applications with React, Node.js "
                       "and PostgreSQL. Founder of Example Studio; most experience comes from contract projects.",
            "skills": ["Frontend: Angular, React, HTML, CSS, TypeScript, JavaScript",
                       "Backend & APIs: Node.js, Express.js, Laravel (PHP), FastAPI",
                       "Databases: MySQL, PostgreSQL, MongoDB, Firestore, SQL query optimization",
                       "Cloud & DevOps: AWS, Google Cloud, Firebase, Docker, Terraform, GitHub Actions, Git",
                       "Automation & Delivery: Agile (Scrum), Jira, Confluence, Figma, workflow automation, code review"],
            "experience": [
                {"role": "Full Stack Developer", "dates": "Mar 2023 – Feb 2024 (11 months)", "bullets": [
                    "Designed and built Taskboard, an internal task-management system with Laravel, PHP and MySQL, used by nearly 500 users.",
                    "Optimized SQL queries and added progressive data loading, cutting the task board load time from over 4 s to under 500 ms.",
                    "Automated task deadline and attendance reminders, reducing late or missed clock-outs by 90%."]},
                {"role": "Website Developer", "dates": "Jan 2019 – Present (7 years, 11 months)", "bullets": [
                    "Rebuilt the company website from legacy WordPress into an Angular and Firebase web app.",
                    "Shipped regular updates and optimizations, and raised site traffic and retention by at least 100x over four years."]},
                {"role": "Web Developer", "dates": "Jun 2021 – Dec 2022 (1 year, 7 months)", "bullets": [
                    "Designed the service-booking logic and secure user accounts for the BookingHub platform.",
                    "Created high-fidelity Figma prototypes and worked with UI designers before implementation."]},
            ],
        },
        "letter": {"paragraphs": list(LETTER)},
    }
    for key, value in over.items():
        s[key] = value
    return s


def odd_jobs_spec():
    s = software_spec()
    s["job"] = {"company": "Example Store", "title": "Sales Associate", "template": "odd-jobs"}
    s["keywords"] = ["Customer Service", "Retail Operations", "Inventory", "Food Safety", "Teamwork",
                     "Communication", "Time Management", "Fast-Paced"]
    s["resume"]["summary"] = ("Sales Associate with 5+ years of experience in customer service and retail operations. "
                              "Founder of Example Studio; most experience comes from contract projects.")
    s["resume"]["skills"] = ["Customer Service", "Retail Operations", "Inventory", "Food Safety", "Teamwork",
                             "Communication", "Time Management", "Fast-Paced Work Environments"]
    s["resume"]["experience"] = [
        {"bullets": ["Assisted customers with mobile devices, computers and accessories.",
                     "Received deliveries and put away stock in the warehouse.",
                     "Supported customers with device setup and service questions."]},
        {"bullets": ["Checked meat products for food safety and quality.", "Restocked shelves and organized inventory."]},
        {"bullets": ["Loaded and unloaded shipments using store equipment.", "Organized overstock inventory."]},
        {"bullets": ["Provided customer service in a fast-paced restaurant.", "Worked with team members on daily tasks."]},
    ]
    return s


class Env:
    """Temp HOME, board DB with one seeded card (code ABCD), fake writer, export folder."""

    def __init__(self, replies=None):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "bin").mkdir()
        fake = t / "bin" / "hermes"
        fake.write_text(FAKE_HERMES)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.calls = t / "calls.jsonl"
        self.queue = t / "queue.json"
        self.queue.write_text(json.dumps(replies or []))
        self.db = t / "kanban.db"
        con = sqlite3.connect(self.db)
        con.executescript("""create table tasks(id text primary key,title text,body text,status text,created_at integer,
            completed_at integer,idempotency_key text);
            create table task_comments(id integer primary key autoincrement,task_id text,author text,body text,created_at integer);""")
        con.execute("insert into tasks values(?,?,?,?,?,?,?)",
                    ("jobtask0001abcd", "Full Stack Developer - Example Corp",
                     "Company: Example Corp | Location: Remote\nLink: https://jobs.example.com/posting/42\n"
                     "Description: Build React and Node.js apps with PostgreSQL.", "todo", 1795000000, None, None))
        con.commit()
        con.close()
        self.export = t / "export"
        (self.export / "Resumes").mkdir(parents=True)
        (self.export / "Cover Letters").mkdir()
        (self.export / "profile.md").write_text("# Profile\n- Software developer, 5+ years.\n", encoding="utf-8")
        (self.export / "odd-jobs-profile.md").write_text("# Odd jobs\n- Sales Associate, Corner Cafe.\n", encoding="utf-8")
        self.env = {**os.environ, "HOME": str(t), "JOBS_BOARD_DB": str(self.db),
                    "JOBS_STATE_DIR": str(t / "state"), "JOBS_EXPORT_DIR": str(self.export),
                    "JOBS_TEMPLATE_DIR": str(TEMPLATES), "HERMES_BIN": str(fake),
                    "RESUME_PYTHON": sys.executable, "FAKE_WRITER_CALLS": str(self.calls),
                    "FAKE_WRITER_QUEUE": str(self.queue)}
        self.tmpdir = t

    def request(self, *args):
        p = subprocess.run([sys.executable, "-I", str(SCRIPTS / "resume_request.py"), *args],
                           env=self.env, capture_output=True, text=True, timeout=900)
        return p.returncode, (p.stdout + p.stderr).strip()

    def render(self, spec, *extra):
        path = self.tmpdir / "spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        p = subprocess.run([sys.executable, "-I", str(SCRIPTS / "resume_render.py"), str(path),
                            "--out-dir", str(self.export), "--template-dir", str(TEMPLATES), *extra],
                           env=self.env, capture_output=True, text=True, timeout=900)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        return p.returncode, json.loads(lines[-1]) if lines else {"raw": p.stdout + p.stderr}

    def state(self, code="ABCD"):
        p = self.tmpdir / "state" / "resume_requests" / f"{code}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def req_dir(self):
        return self.tmpdir / "state" / "resume_requests"

    def chat(self, code="ABCD"):
        p = self.req_dir() / f"{code}.chat.json"
        return json.loads(p.read_text(encoding="utf-8"))["messages"] if p.exists() else []

    def calls_made(self):
        if not self.calls.exists():
            return []
        return [json.loads(ln) for ln in self.calls.read_text(encoding="utf-8").splitlines()]

    def files(self, folder):
        return sorted(p.name for p in (self.export / folder).iterdir())

    def close(self):
        self.tmp.cleanup()


def pdf_text(path):
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True).stdout


class PureRules(unittest.TestCase):
    """Checks that need no rendering."""

    def test_names_follow_agents_rules(self):
        self.assertEqual(rr.resume_name("Example Power Co", "Apprentice Technician"),
                         "Resume-Jane-Doe-Example-Power-Co-Apprentice-Technician.pdf")
        self.assertEqual(rr.resume_name("Initech", "Software Engineer II"),
                         "Resume-Jane-Doe-Initech-Software-Engineer-II.pdf")
        self.assertEqual(rr.letter_name("Umbrella Labs AI"), "Cover-Letter-Jane-Doe-Umbrella-Labs-AI.pdf")
        self.assertEqual(rr.slug("a/b:c*?"), "a-b-c")

    def test_free_path_never_picks_an_existing_name(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "Cover-Letter-Jane-Doe-X.pdf").write_bytes(b"old")
            (folder / "Cover-Letter-Jane-Doe-X-2.pdf").write_bytes(b"old")
            self.assertEqual(rr.free_path(folder, "Cover-Letter-Jane-Doe-X.pdf").name,
                             "Cover-Letter-Jane-Doe-X-3.pdf")

    def test_banned_words_do_not_reject_normal_skills(self):
        for text in ("high availability systems", "Objective-C", "availability zones", "TLS certificate"):
            hits = [lbl for pat, lbl in rr.banned() if re.search(pat, text, re.I) and lbl != "TLS"]
            self.assertEqual(hits, [], text)
        for text in ("my availability", "Objective", "certifications", "Computing Science"):
            self.assertTrue(any(re.search(pat, text, re.I) for pat, _ in rr.banned()), text)

    def test_non_object_sections_are_a_clean_error(self):
        bad = software_spec()
        bad["resume"] = []
        self.assertEqual(rr.validate_spec(bad, 3), ["resume must be a JSON object"])

    def test_spec_rules(self):
        good = software_spec()
        self.assertEqual(rr.validate_spec(good, 3), [])
        bad = software_spec()
        bad["resume"]["summary"] = "Experienced developer with 5+ years of experience at Example Studio."
        self.assertTrue(any("start with the exact posting title" in e for e in rr.validate_spec(bad, 3)))
        bad = software_spec()
        bad["keywords"] = bad["keywords"][:5]
        self.assertTrue(any("keywords" in e for e in rr.validate_spec(bad, 3)))
        bad = software_spec()
        bad["resume"]["summary"] = "Full Stack Developer with 5+ years of experience, founder of a startup."
        self.assertTrue(any("Example Studio" in e for e in rr.validate_spec(bad, 3)))
        self.assertEqual(rr.validate_spec(odd_jobs_spec(), 4), [])
        self.assertTrue(any("exactly 4 roles" in e for e in rr.validate_spec(software_spec(), 4)))


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.close()

    def test_software_render_writes_named_files_and_one_page(self):
        code, res = self.env.render(software_spec())
        self.assertEqual(code, 0, res)
        self.assertEqual(res["pages"], {"resume": 1, "letter": 1})
        self.assertEqual(self.env.files("Resumes"), [
            "Resume-Jane-Doe-Example-Corp-Full-Stack-Developer.docx",
            "Resume-Jane-Doe-Example-Corp-Full-Stack-Developer.pdf"])
        self.assertEqual(self.env.files("Cover Letters"), [
            "Cover-Letter-Jane-Doe-Example-Corp.docx", "Cover-Letter-Jane-Doe-Example-Corp.pdf"])
        text = pdf_text(res["resume_pdf"])
        self.assertIn("BSc Computer Science", text)
        self.assertNotIn("Computing Science", text)
        self.assertNotIn("Tailor this section", text)
        self.assertIn("Full Stack Developer with 5+ years of experience", text)

    def test_banned_skill_is_rejected_and_nothing_written(self):
        spec = software_spec()
        spec["resume"]["experience"][0]["bullets"][0] = "Built Spring Boot services for Taskboard."
        code, res = self.env.render(spec)
        self.assertEqual(code, 1)
        self.assertTrue(any("Spring Boot" in e for e in res["errors"]), res)
        self.assertEqual(self.env.files("Resumes"), [])
        self.assertEqual(self.env.files("Cover Letters"), [])

    def test_one_page_limit_is_enforced(self):
        spec = software_spec()
        long = "Delivered a large, detailed set of results across many modules and teams, documenting each step in full. " * 2
        for role in spec["resume"]["experience"]:
            role["bullets"] = [long] * 5
        code, res = self.env.render(spec)
        self.assertEqual(code, 1)
        self.assertTrue(any("pages" in e for e in res["errors"]), res)
        self.assertEqual(self.env.files("Resumes"), [])

    def test_existing_files_are_not_overwritten(self):
        old = self.env.export / "Resumes" / "Resume-Jane-Doe-Example-Corp-Full-Stack-Developer.pdf"
        old.write_bytes(b"another job's resume")
        code, res = self.env.render(software_spec())
        self.assertEqual(code, 0, res)
        self.assertEqual(old.read_bytes(), b"another job's resume")
        self.assertTrue(res["resume_pdf"].endswith("Resume-Jane-Doe-Example-Corp-Full-Stack-Developer-2.pdf"))

    def test_same_job_replace_overwrites_only_its_own_file(self):
        code, first = self.env.render(software_spec())
        self.assertEqual(code, 0, first)
        spec = software_spec()
        spec["resume"]["summary"] = spec["resume"]["summary"].replace("contract projects", "client projects")
        code, second = self.env.render(spec, "--replace-resume", first["resume_pdf"],
                                       "--replace-letter", first["letter_pdf"])
        self.assertEqual(code, 0, second)
        self.assertEqual(second["resume_pdf"], first["resume_pdf"])
        self.assertEqual(self.env.files("Resumes").count("Resume-Jane-Doe-Example-Corp-Full-Stack-Developer.pdf"), 1)
        self.assertNotIn("-2.pdf", " ".join(self.env.files("Resumes")))

    def test_too_few_keywords_is_rejected(self):
        spec = software_spec()
        spec["keywords"] = ["Kubernetes", "Terraform", "Rust", "Go", "Haskell", "Erlang", "Scala", "Elixir", "React"]
        code, res = self.env.render(spec)
        self.assertEqual(code, 1)
        self.assertTrue(any("need 8" in e for e in res["errors"]), res)

    def test_odd_jobs_template_keeps_reason_lines(self):
        code, res = self.env.render(odd_jobs_spec())
        self.assertEqual(code, 0, res)
        text = pdf_text(res["resume_pdf"])
        self.assertIn("Reason for leaving: Placeholder reason one.", text)
        self.assertIn("Barista (Permanent Part Time), Corner Cafe", text)
        self.assertIn("Loaded and unloaded shipments", text)


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.env = None

    def tearDown(self):
        if self.env:
            self.env.close()

    def test_bad_draft_is_retried_with_the_failed_checks(self):
        bad = software_spec()
        bad["resume"]["experience"][0]["bullets"][0] = "Built Spring Boot services for Taskboard."
        self.env = Env([json.dumps(bad), json.dumps(software_spec())])
        code, out = self.env.request("ABCD")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("ready ABCD"), out)
        calls = self.env.calls_made()
        self.assertEqual(len(calls), 2)
        self.assertIn("-t", calls[0]["argv"])
        self.assertEqual(calls[0]["argv"][calls[0]["argv"].index("-t") + 1], "web")
        self.assertIn("FAILED THESE CHECKS", calls[1]["prompt"])
        self.assertIn("Spring Boot", calls[1]["prompt"])
        self.assertEqual(self.env.state()["status"], "ready")

    def test_needs_input_then_answer_completes(self):
        questions = {"status": "NEEDS_INPUT", "questions": ["Do you have Docker production experience?"]}
        self.env = Env([json.dumps(questions), json.dumps(software_spec())])
        code, out = self.env.request("ABCD")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("needs_input ABCD"), out)
        state = self.env.state()
        self.assertEqual(state["status"], "needs_input")
        self.assertEqual(state["files"], {})
        self.assertEqual(self.env.files("Resumes"), [])
        code, out = self.env.request("ABCD", "--answer", "1=Yes, two years in production")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("ready ABCD"), out)
        self.assertIn("Answer: Yes, two years in production", self.env.calls_made()[1]["prompt"])

    def test_feedback_overwrites_the_same_jobs_draft_and_approve_works(self):
        self.env = Env([json.dumps(software_spec()), json.dumps(software_spec())])
        code, out = self.env.request("ABCD")
        self.assertTrue(out.startswith("ready"), out)
        first = self.env.state()["files"]
        code, out = self.env.request("ABCD", "--feedback", "make the summary shorter")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.env.state()["files"]["resume_pdf"], first["resume_pdf"])
        self.assertEqual(len(self.env.files("Resumes")), 2)
        self.assertIn("make the summary shorter", self.env.calls_made()[1]["prompt"])
        code, out = self.env.request("ABCD", "--approve")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.env.state()["status"], "approved")
        code, out = self.env.request("ABCD", "--approve")
        self.assertEqual(code, 1, out)

    def test_unparseable_output_fails_after_three_attempts(self):
        self.env = Env(["not json", "still not json", "nope"])
        code, out = self.env.request("ABCD")
        self.assertEqual(code, 1, out)
        self.assertTrue(out.startswith("failed ABCD"), out)
        self.assertEqual(self.env.state()["status"], "failed")
        self.assertEqual(len(self.env.calls_made()), 3)

    def test_live_lock_blocks_a_second_run(self):
        self.env = Env([json.dumps(software_spec())])
        lock = self.env.tmpdir / "state" / "resume_requests" / "ABCD.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        sleeper = live_run()
        try:
            lock.write_text(str(sleeper.pid))
            code, out = self.env.request("ABCD")
        finally:
            sleeper.kill()
        self.assertEqual(code, 3, out)
        self.assertTrue(out.startswith("locked"), out)
        self.assertEqual(self.env.calls_made(), [])

    def test_a_reused_pid_is_not_a_live_run(self):
        self.env = Env([json.dumps(software_spec())])
        lock = self.env.tmpdir / "state" / "resume_requests" / "ABCD.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(os.getpid()))                       # alive, but it is not resume_request.py
        code, out = self.env.request("ABCD")
        self.assertEqual(code, 0, out)

    def test_stale_lock_is_ignored(self):
        self.env = Env([json.dumps(software_spec())])
        lock = self.env.tmpdir / "state" / "resume_requests" / "ABCD.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("999999999")
        code, out = self.env.request("ABCD")
        self.assertEqual(code, 0, out)
        self.assertFalse(lock.exists())

    def test_status_and_unknown_code(self):
        self.env = Env([])
        code, out = self.env.request("ABCD", "--status")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("none ABCD"), out)
        code, out = self.env.request("ZZZZ")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("failed ZZZZ"), out)


SHORT_SUMMARY = ("Full Stack Developer with 5+ years of experience in React and Node.js. "
                 "Founder of Example Studio; most experience comes from contract projects.")


def short_spec(note=None):
    s = software_spec()
    s["resume"]["summary"] = SHORT_SUMMARY
    if note:
        s["message"] = note
    return s


def live_run():
    """A process that looks like a running resume_request.py to the lock check."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "resume_request.py", "ABCD"])


def _friendly(text):
    sys.path.insert(0, str(SCRIPTS))
    import resume_request as RQ
    return RQ.friendly_error(text)


class ChatTests(unittest.TestCase):
    """The chat log, version snapshots, replies, undo and Stop that the board's Messages screen uses."""

    def setUp(self):
        self.env = None

    def tearDown(self):
        if self.env:
            self.env.close()

    def draft(self, more=()):
        self.env = Env([json.dumps(software_spec()), *more])
        code, out = self.env.request("ABCD")
        self.assertEqual(code, 0, out)
        return self.env

    def test_draft_logs_an_agent_message_and_snapshots_v1(self):
        e = self.draft()
        st = e.state()
        self.assertEqual(st["version"], 1)
        self.assertEqual([v["kind"] for v in st["versions"]], ["draft"])
        self.assertTrue((e.req_dir() / "ABCD.spec.v1.json").exists())
        msgs = e.chat()
        self.assertEqual([m["role"] for m in msgs], ["agent"])
        self.assertEqual(msgs[0]["version"], 1)
        self.assertNotIn("message", json.loads((e.req_dir() / "ABCD.spec.json").read_text()))

    def test_message_revises_in_place_and_uses_the_writers_note(self):
        e = self.draft([json.dumps(short_spec("I shortened the summary."))])
        first = e.state()["files"]
        code, out = e.request("ABCD", "--message", "make the summary shorter")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("ready ABCD"), out)
        st = e.state()
        self.assertEqual(st["files"], first)
        self.assertEqual(len(e.files("Resumes")), 2)
        self.assertEqual((st["version"], st["versions"][-1]["kind"], st["versions"][-1]["base"]), (2, "revision", 1))
        prompt = e.calls_made()[1]["prompt"]
        self.assertIn("make the summary shorter", prompt)
        self.assertIn("CONVERSATION SO FAR", prompt)
        self.assertIn("REVISION: previous draft", prompt)
        msgs = e.chat()
        self.assertEqual([m["role"] for m in msgs], ["agent", "user", "agent"])
        self.assertEqual(msgs[1]["text"], "make the summary shorter")
        self.assertEqual((msgs[2]["text"], msgs[2]["version"]), ("I shortened the summary.", 2))
        self.assertIn("experience in React and Node.js", pdf_text(first["resume_pdf"]))

    def test_a_pure_question_gets_a_reply_and_changes_nothing(self):
        e = self.draft([json.dumps({"status": "REPLY", "message": "Yes, it is one page."})])
        before = e.state()["files"]
        code, out = e.request("ABCD", "--message", "is it one page?")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("reply ABCD"), out)
        st = e.state()
        self.assertEqual((st["status"], st["version"], st["files"]), ("ready", 1, before))
        self.assertFalse((e.req_dir() / "ABCD.spec.v2.json").exists())
        self.assertEqual(e.chat()[-1]["text"], "Yes, it is one page.")

    def test_reply_is_refused_without_a_draft(self):
        self.env = Env([json.dumps({"status": "REPLY", "message": "hi"})] * 3)
        code, out = self.env.request("ABCD")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.env.state()["status"], "failed")

    def test_undo_goes_back_as_a_new_version_once(self):
        e = self.draft([json.dumps(short_spec())])
        e.request("ABCD", "--message", "shorter please")
        code, out = e.request("ABCD", "--undo")
        self.assertEqual(code, 0, out)
        st = e.state()
        self.assertEqual((st["version"], st["status"]), (3, "ready"))
        self.assertEqual((st["versions"][-1]["kind"], st["versions"][-1]["base"]), ("undo", 1))
        self.assertEqual(json.loads((e.req_dir() / "ABCD.spec.v3.json").read_text()),
                         json.loads((e.req_dir() / "ABCD.spec.v1.json").read_text()))
        self.assertNotIn("experience in React and Node.js", pdf_text(st["files"]["resume_pdf"]))
        self.assertEqual(len(e.files("Resumes")), 2)
        self.assertEqual(e.chat()[-1]["version"], 3)
        code, out = e.request("ABCD", "--undo")
        self.assertEqual(code, 1, out)
        self.assertEqual(e.state()["version"], 3)

    def test_undo_needs_a_change_to_undo(self):
        e = self.draft()
        code, out = e.request("ABCD", "--undo")
        self.assertEqual(code, 1, out)
        self.assertEqual(e.state()["version"], 1)

    def test_a_failed_revision_keeps_the_last_good_files_and_says_so(self):
        e = self.draft(["no", "no", "no"])
        before = e.state()["files"]
        code, out = e.request("ABCD", "--message", "add Spring Boot")
        self.assertEqual(code, 1, out)
        st = e.state()
        self.assertEqual((st["status"], st["version"], st["files"]), ("ready", 1, before))
        self.assertIn("still v1", e.chat()[-1]["text"])
        self.assertTrue(Path(before["resume_pdf"]).exists())
        # the next revision starts from the last good spec, not from the failed attempt
        e.env["FAKE_WRITER_SLEEP"] = "0"
        Path(e.queue).write_text(json.dumps([json.dumps(short_spec())]))
        code, out = e.request("ABCD", "--message", "try again, shorter")
        self.assertEqual(code, 0, out)
        self.assertEqual(e.state()["version"], 2)

    def test_message_answers_needs_input_and_questions_are_posted(self):
        q = {"status": "NEEDS_INPUT", "questions": ["Docker in production?", "Notice period?"]}
        self.env = Env([json.dumps(q), json.dumps(software_spec())])
        code, out = self.env.request("ABCD")
        self.assertEqual(self.env.state()["status"], "needs_input")
        first = self.env.chat()[0]
        self.assertEqual((first["role"], first["questions"]), ("agent", q["questions"]))
        self.assertIn("1. Docker in production?", first["text"])
        code, out = self.env.request("ABCD", "--message", "Yes two years, and none for the second")
        self.assertEqual(code, 0, out)
        prompt = self.env.calls_made()[1]["prompt"]
        self.assertIn("Yes two years, and none for the second", prompt)
        self.assertIn("see his reply below", prompt)
        self.assertEqual(self.env.state()["status"], "ready")

    def test_legacy_ready_draft_becomes_v1_before_a_revision(self):
        e = self.draft([json.dumps(short_spec())])
        st = e.state()
        for k in ("version", "versions"):
            st.pop(k)
        (e.req_dir() / "ABCD.spec.v1.json").unlink()
        (e.req_dir() / "ABCD.json").write_text(json.dumps(st))
        code, out = e.request("ABCD", "--message", "shorter")
        self.assertEqual(code, 0, out)
        st = e.state()
        self.assertEqual([v["n"] for v in st["versions"]], [1, 2])
        self.assertTrue((e.req_dir() / "ABCD.spec.v1.json").exists())

    def test_approve_note_is_quiet_and_says_nothing_was_applied(self):
        e = self.draft()
        e.request("ABCD", "--approve")
        last = e.chat()[-1]
        self.assertTrue(last["quiet"])
        self.assertIn("haven't applied", last["text"])

    def test_stop_returns_to_the_last_good_state(self):
        import signal
        import time
        e = self.draft([json.dumps(short_spec())])
        before = e.state()["files"]
        e.env["FAKE_WRITER_SLEEP"] = "60"
        proc = subprocess.Popen([sys.executable, "-I", str(SCRIPTS / "resume_request.py"), "ABCD", "--message", "shorter"],
                                env=e.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                start_new_session=True)
        try:
            for _ in range(100):
                if e.state().get("status") == "writing_resume" and len(e.calls_made()) >= 1 and \
                        (e.req_dir() / "ABCD.prompt.txt").exists() and "shorter" in (e.req_dir() / "ABCD.prompt.txt").read_text():
                    break
                time.sleep(0.1)
            time.sleep(0.3)
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
        st = e.state()
        self.assertEqual((st["status"], st["version"], st["files"]), ("ready", 1, before))
        self.assertFalse((e.req_dir() / "ABCD.lock").exists())
        self.assertTrue(e.chat()[-1]["quiet"])
        self.assertIn("Stopped", e.chat()[-1]["text"])

    def test_approve_waits_for_a_running_job_and_leaves_no_temp_files(self):
        e = self.draft()
        lock = e.req_dir() / "ABCD.lock"
        sleeper = live_run()                                   # a live run holds the lock
        try:
            lock.write_text(str(sleeper.pid))
            code, out = e.request("ABCD", "--approve")
        finally:
            sleeper.kill()
        self.assertEqual(code, 3, out)
        self.assertEqual(e.state()["status"], "ready")
        lock.unlink()
        code, out = e.request("ABCD", "--approve")
        self.assertEqual(code, 0, out)
        self.assertEqual(list(e.req_dir().glob("*.tmp")), [])

    def test_chat_text_has_no_local_paths(self):
        e = self.draft()
        self.assertEqual(_friendly("missing /home/user/Documents/profile.md, and/or ~/x/y.md"), "missing a file, and/or a file")
        self.assertEqual(_friendly("see https://x.example/a/b and file:///home/u/x/y"), "see https://x.example/a/b and a file")
        old, os.environ["JOBS_EXPORT_DIR"] = os.environ.get("JOBS_EXPORT_DIR"), str(e.export)
        try:                                                    # the export folder is a known root, so a name with a space goes whole
            self.assertEqual(_friendly(f"missing {e.export}/Resumes/My Resume.pdf now"), "missing a file now")
            self.assertEqual(_friendly(f"missing {e.export}/Cover Letters/Cover-Letter-X.pdf now"), "missing a file now")
        finally:
            if old is None:
                del os.environ["JOBS_EXPORT_DIR"]
            else:
                os.environ["JOBS_EXPORT_DIR"] = old

    def test_a_revision_that_asks_questions_ends_in_needs_input(self):
        q = {"status": "NEEDS_INPUT", "questions": ["Docker in production?"]}
        e = self.draft([json.dumps(q)])
        code, out = e.request("ABCD", "--message", "add docker")
        st = e.state()
        self.assertEqual(st["status"], "needs_input", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
