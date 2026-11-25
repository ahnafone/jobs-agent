#!/usr/bin/env python3
"""Regenerate demo/fixtures.json: a snapshot of what the board's API returns for a made-up job search.

Maintainer tool, not needed to use the demo. It seeds a throwaway board (temp folder, fake `hermes`) with invented
companies through the real scripts, then calls the real board code to produce /api/state, /api/activity and
/api/messages. Nothing here reads your real board.

  python3 demo/make_fixtures.py
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["JOBS_AGENT_CONFIG"] = str(ROOT / "tests" / "fixtures" / "job-agent.test.json")
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "board"))
from test_jobs_helpers import Env  # noqa: E402  (temp board + fake hermes)

DAY = 86400
# (title, company, location, fit, source, pay, reason, summary, requirements, how many days ago it was added)
JOBS = [
    ("Junior Web Developer", "Northwind Apps", "Vancouver, BC", 86, "email", "CAD 68-76K",
     "React and Node match your projects; hybrid in Vancouver.", "Build customer-facing features for a booking platform.",
     "1+ year with React; Node.js; REST APIs; SQL", 12),
    ("QA Analyst", "Lumen Health Tech", "Burnaby, BC", 78, "email", "CAD 62-70K",
     "Test automation and API testing are in your profile.", "Own regression testing for a clinic scheduling product.",
     "Test plans; Postman or similar; basic SQL; Jira", 11),
    ("Software Developer I", "Cedar Logistics", "Remote, Canada", 82, "discord-link", "CAD 70-80K",
     "Remote in Canada; Python and cloud basics fit.", "Work on route-planning services in Python and AWS.",
     "Python; REST APIs; Git; AWS basics", 9),
    ("IT Support Specialist", "Harbourline Credit Union", "Surrey, BC", 70, "email", "CAD 58-64K",
     "Close to home; help desk plus light scripting.", "First-line support for branch staff and member kiosks.",
     "Help desk; Windows and macOS; ticketing; customer service", 8),
    ("Full Stack Developer", "Pinecone Studio", "Vancouver, BC", 88, "discord-link", "CAD 72-82K",
     "Angular, Node and Firebase are all in your stack.", "Small team shipping a recipe-planning app.",
     "Angular or React; Node.js; Firebase or SQL; Git", 7),
    ("Technical Support Analyst", "Brightpath Software", "Remote, Canada", 74, "email", "CAD 60-68K",
     "Remote; troubleshooting and API knowledge help.", "Second-line support for a payroll SaaS.",
     "Troubleshooting; SQL; REST; written communication", 6),
    ("Junior Backend Developer", "Orca Payments", "Vancouver, BC", 80, "board", "CAD 70-78K",
     "Node and SQL match; fintech is a stretch but fine.", "Build internal APIs for merchant onboarding.",
     "Node.js or Python; SQL; REST; unit testing", 5),
    ("Automation Engineer (QA)", "Tidewater Robotics", "Richmond, BC", 76, "email", "CAD 66-74K",
     "Automation and CI experience fit; on-site 3 days.", "Write test automation for warehouse robot software.",
     "Python or JS; CI/CD; test frameworks; Linux", 4),
    ("Web Developer", "Maple Leaf Outdoors", "Surrey, BC", 72, "discord-link", "CAD 64-72K",
     "Surrey on-site; WordPress and JS.", "Maintain an e-commerce site and build landing pages.",
     "JavaScript; PHP or WordPress; HTML/CSS; Git", 3),
    ("Support Engineer", "Quillstone Data", "Remote, Canada", 69, "email", "CAD 65-72K",
     "Remote support role with SQL and API work.", "Help customers debug integrations with a reporting API.",
     "SQL; REST; Postman; clear writing", 2),
]
# Pending in the Inbox (review): these arrive via --review
INBOX = [
    ("Frontend Developer", "Sparrow Fintech", "Vancouver, BC", 81, "email", "CAD 70-80K",
     "Angular and TypeScript match; hybrid.", "Build dashboards for small-business accounting.",
     "TypeScript; Angular or React; REST", 1),
    ("QA Tester", "Glasshouse Games", "Burnaby, BC", 66, "email", "CAD 55-62K",
     "Entry level; testing skills transfer.", "Playtest and log defects for a mobile puzzle game.",
     "Attention to detail; bug reports; Jira", 1),
]
# (card index in JOBS, action, days ago, note, deadline in days from today or None)
MOVES = [
    (0, "applied", 10, "", None),
    (0, "response:interview", 4, "Phone screen with the hiring manager", 3),
    (0, "stage:interview", 4, "Phone screen booked", None),
    (1, "applied", 9, "", None),
    (1, "response:info-request", 8, "Recruiter asked for availability", None),
    (1, "response:assessment", 2, "Take-home API testing task", 2),
    (2, "applied", 7, "", None),
    (3, "applied", 6, "", None),
    (3, "response:rejection", 1, "Went with another candidate", None),
    (4, "applied", 3, "", None),
    (5, "stage:parked", 5, "Pay is below the target range", None),
]


def backdate(db, after_id, ago_days):
    """Make the comments written by the last script call look like they happened `ago_days` ago."""
    from datetime import date, datetime, timedelta
    con = sqlite3.connect(db)
    stamp = int(datetime.now().timestamp()) - ago_days * DAY
    day = (date.today() - timedelta(days=ago_days)).isoformat()
    for cid, tid, body in con.execute("select id,task_id,body from task_comments where id>?", (after_id,)).fetchall():
        con.execute("update task_comments set created_at=?, body=? where id=?", (stamp, body.replace(date.today().isoformat(), day), cid))
        if body.startswith("applied "):
            con.execute("update tasks set completed_at=? where id=?", (stamp, tid))
    con.commit()
    con.close()


def main():
    e = Env()
    tmp = Path(e.tmp.name)
    # Point this process at the same temp board so the board's own code builds the snapshot.
    os.environ.update({k: e.env[k] for k in ("HOME", "JOBS_BOARD_DB", "JOBS_STATE_DIR", "JOBS_EXPORT_DIR", "HERMES_BIN")})
    codes = {}

    def add(row, review=False):
        title, company, loc, fit, src, pay, reason, summary, reqs, _ago = row
        args = ["--title", title, "--company", company, "--location", loc, "--fit", str(fit), "--reason", reason,
                "--source", src, "--pay", pay, "--summary", summary, "--requirements", reqs,
                "--link", "https://careers.example/" + company.lower().replace(" ", "-") + "/" + title.lower().replace(" ", "-")]
        if review:
            args.append("--review")
        out = e.run("jobs_add.py", *args)
        assert "CREATED" in out, out
        return out.split("[", 1)[1].split("]", 1)[0]

    for i, row in enumerate(JOBS):
        codes[i] = add(row)
    for row in INBOX:
        add(row, review=True)
    from datetime import date, timedelta
    for idx, act, ago_days, note, dl in MOVES:
        code = codes[idx]
        when = (date.today() - timedelta(days=ago_days)).isoformat()
        args = [code, "--actor", "you", "--date", when]
        if act == "applied":
            args += ["--applied", "--by", "user"]
        elif act.startswith("response:"):
            args += ["--response", act.split(":")[1], "--note", note]
            if dl:
                args += ["--deadline", (date.today() + timedelta(days=dl)).isoformat()]
        else:
            args += ["--stage", act.split(":")[1], "--note", note]
        con = sqlite3.connect(e.db)
        last = con.execute("select coalesce(max(id),0) from task_comments").fetchone()[0]
        con.close()
        out = e.run("jobs_mark.py", *args)
        assert "Traceback" not in out and "error" not in out.lower(), out
        backdate(e.db, last, ago_days)
    e.run("jobs_mark.py", codes[0], "--actor", "you", "--add-note", "Recruiter said the team uses React and Node. Ask about hybrid days.")

    # Spread the history over the last two weeks, oldest jobs first.
    con = sqlite3.connect(e.db)
    rows = con.execute("select id,title from tasks order by created_at, rowid").fetchall()
    ago = {}
    for row in JOBS + INBOX:
        ago[row[0]] = row[-1]
    for n, (tid, title) in enumerate(rows):
        days = next((v for k, v in ago.items() if k in title), 1)
        con.execute("update tasks set created_at=created_at-? where id=?", (days * DAY, tid))
        for k, (cid, cts) in enumerate(con.execute("select id,created_at from task_comments where task_id=? order by id", (tid,)).fetchall()):
            if cts > int(__import__("time").time()) - 3600:      # still "just now": the creation-time comments
                con.execute("update task_comments set created_at=? where id=?", (cts - days * DAY + k * 3600, cid))
    con.commit()
    con.close()

    import jobs_board as B
    state = B.build_state()
    state["lastChecked"] = state["now"] - 3 * 3600
    snap = {
        "state": state,
        "activity": B.activity(60),
        "messages": B.messages_summary(),
        "today": state["today"], "now": state["now"],
    }
    out = ROOT / "demo" / "fixtures.json"
    out.write_text(json.dumps(snap, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(state['cards'])} cards, {len(state['inbox'])} inbox, {len(snap['activity'])} activity rows)")
    e.tmp.cleanup()


if __name__ == "__main__":
    main()
