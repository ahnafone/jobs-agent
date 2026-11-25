#!/usr/bin/env python3
"""Eligibility gate: decides whether a job is worth reading/carding BEFORE any research is spent on it.

Usage:
  jobs_gate.py --title T [--company C] [--location L] [--url U] [--text TEXT] [--log]
  jobs_gate.py --batch-file FILE [--log]       # lines: title | company | location | url

Stages (picked automatically):
  title stage  (no --text): rejects obvious non-fits from title + location alone.
                            Prints `PASS-TITLE: open the page` or `REJECT: <reason>`.
  page stage   (--text = requirement lines / description): also checks degree, years, citizenship,
                            clearance, licence, US-only. Prints `ELIGIBLE [(notes)]` or `REJECT: <reason>`.

Degree rule: the user holds a computer-science degree (edit ok_degree_fields in the rules file for yours), so a degree requirement passes when its field is
computer science / IT / software / engineering / maths / data, or any or unspecified discipline. A
required degree in another field (nursing, accounting, marketing...) is a REJECT, as is a required
master's or PhD. Roles needing only high school (or nothing) always pass this check.

--log appends rejections to ~/.hermes/state/job_rejects.jsonl (shown by jobs_list.py --rejects).
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_common as C  # noqa: E402

RULES_FILE = Path(__file__).resolve().parent / "jobs_gate_rules.json"
_rules = None


def rules():
    global _rules
    if _rules is None:
        _rules = json.loads(RULES_FILE.read_text())
    return _rules


def _places_re(items):
    return re.compile(r"(?i)(?<![a-z])(" + "|".join(items) + r")(?![a-z])")


PROV_CODES = "ON|AB|QC|MB|SK|NS|NB|NL|PE|YT|NT|NU"


def eval_location(location, title=""):
    """Returns ('ok'|'reject'|'unknown', reason)."""
    r = rules()
    loc = (location or "").strip()
    if not loc:
        return "unknown", ""
    low = loc.lower()
    remote = bool(re.search(r"(?i)\bremote|work from home|\bwfh\b|anywhere", loc))
    marker = bool(re.search(r"(?i)\b(bc|british columbia|canada)\b", loc))
    home = _places_re([re.escape(p) for p in r["home_places"]]).search(low.replace("richmond hill", ""))
    foreign = _places_re(r["foreign_places"]).search(low)
    state = re.search(r"(?:,|\()\s*(" + r["us_state_codes"] + r")\b\.?(?:\s*\d{5})?", loc)
    prov = re.search(r"(?:,|\()\s*(" + PROV_CODES + r")\b", loc)
    other = _places_re([re.escape(p) for p in r["other_places"]]).search(low)
    bad = foreign or state
    if bad and not marker:
        what = bad.group(0).strip(" ,(")
        return "reject", (f"remote but not open to Canada ({what})" if remote else f"location is outside Canada ({what})")
    if (other or prov) and not home:
        what = (other or prov).group(0).strip(" ,(")
        if remote and "canada" in low:
            return "ok", ""
        return "reject", f"location is outside the Lower Mainland ({what})"
    if home or remote:
        return "ok", ""
    return "unknown", ""


def _matches(rule_list, text):
    for rule in rule_list:
        m = re.search(rule["re"], text, flags=re.I)
        if not m:
            continue
        unless = rule.get("unless")
        if unless and re.search(unless, text, flags=re.I):
            continue
        return rule["why"], m.group(0)
    return None, None


YEARS_SKIP = re.compile(r"(?i)prefer|asset|nice to have|bonus|an advantage|ideally|\b(we|our|us)\b|founded|since \d{4}|\bcompany\b")


ABOUT_US = re.compile(r"(?i)\b(we|our|us)\b(?!['\u2019]re looking|\s+(are|is) looking|\s+require|\s+need)|founded|since \d{4}|\bcompany\b")
SOFT = re.compile(r"(?i)prefer|asset|nice to have|bonus|an advantage|ideally")


def _years(text, verbatim=False):
    """Highest required years of experience mentioned (ignoring preferred / asset lines, and about-us lines
    when the text is a free-form description rather than the posting's own requirement lines)."""
    best = 0
    for line in re.split(r"[\n;\u2022*]|\.(?=\s|$)", text):
        if SOFT.search(line) or (not verbatim and ABOUT_US.search(line)):
            continue
        if re.search(r"(?i)\bage\b|years? old", line):
            continue
        if not re.search(r"(?i)experience|exp\b|minimum|at least|\bmin\b", line):
            continue
        for m in re.finditer(r"(?i)(\d{1,2})\s*\+?\s*(?:(?:to|-|\u2013)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b", line):
            v = int(m.group(1))
            if v <= 30 and not re.match(r"(?i)\s*(of age|old)", line[m.end():]):
                best = max(best, v)
    return best


ABBREV = [(r"(?i)\bb\.?\s?sc\.?", "bsc"), (r"(?i)\bm\.?\s?sc\.?", "msc"), (r"(?i)\bph\.?\s?d\.?", "phd"),
          (r"(?i)\bb\.?\s?eng\.?", "beng"), (r"(?i)\bm\.?\s?eng\.?", "meng"), (r"(?i)\bm\.?\s?b\.?\s?a\.?\b", "mba")]
DEG_RE = re.compile(r"(?i)\b(bachelor|bsc|beng|undergraduate|university degree|college degree|degree|diploma|post[- ]secondary|"
                    r"msc|meng|mba|phd|doctorate|master'?s\s+(?:degree|of|in|program)|master['\u2019]s)")
HIGH_RE = re.compile(r"(?i)\b(msc|meng|mba|phd|doctorate|master'?s?\s+(?:degree|of|in|program)|master['\u2019]s)\b")
LOW_RE = re.compile(r"(?i)\b(bachelor|bsc|beng|undergraduate|university degree|college degree|diploma)")
OPTIONAL_RE = re.compile(r"(?i)prefer|asset|nice to have|bonus|an advantage|ideal|or equivalent|in lieu|equivalent (work |practical )?experience|considered")


def _degree_check(text):
    """Returns (reject_reason_or_None, unclear_note_or_None)."""
    r = rules()
    ok_re = re.compile(r["ok_degree_fields"], re.I)
    bad_re = re.compile(r["bad_degree_fields"], re.I)
    for pat, rep in ABBREV:
        text = re.sub(pat, rep, text)
    note = None
    for seg in re.split(r"[\n;\u2022*]|\.(?=\s|$)", text):
        if not DEG_RE.search(seg) or OPTIONAL_RE.search(seg):
            continue
        high = HIGH_RE.search(seg)
        if high and not LOW_RE.search(seg) and not re.search(r"(?i)\bor\b", seg):
            return f"requires a {high.group(1).lower()}-level degree", None
        fm = re.search(r"(?i)(?:degree|bachelor'?s?|bsc|beng|diploma|master'?s?|msc|meng|certificate)\s+(?:degree\s+)?(?:in|of)\s+(.{2,200}?)(?=\s+(?:with|and\s+\d|plus|from|who|that)\b|[;:]|$)", seg)
        if not fm:
            continue                     # degree required, field not stated: loose pass
        field = fm.group(1)
        if ok_re.search(field):
            continue                     # any listed field in the user's area passes
        if bad_re.search(field):
            return f"requires a degree/diploma in {re.split(r'[,(]', field)[0].strip()[:60]}", None
        note = "degree field unclear"
    return None, note


def evaluate(title, company="", location="", text="", source="", req_text=None):
    """-> dict(verdict='reject'|'pass-title'|'eligible', reason, notes[]).
    text: everything known about the job (citizenship / clearance / US-only rules run on it).
    req_text: only the requirement lines (degree and years rules); defaults to text."""
    r = rules()
    title = (title or "").strip()
    notes = []
    why, hit = _matches(r["title_reject"], title)
    if why:
        return {"verdict": "reject", "reason": f"{why} ('{hit}')", "notes": notes}
    verdict, loc_reason = eval_location(location, title)
    if verdict == "reject":
        return {"verdict": "reject", "reason": loc_reason, "notes": notes}
    text = (text or "").strip()
    if not text:
        return {"verdict": "pass-title", "reason": "", "notes": ["location unknown"] if verdict == "unknown" else notes}
    req = text if not (req_text or "").strip() else req_text
    why, hit = _matches(r["text_reject"], text)
    if why:
        return {"verdict": "reject", "reason": f"{why} ('{hit[:60]}')", "notes": notes}
    deg, deg_note = _degree_check(req)
    if deg:
        return {"verdict": "reject", "reason": deg + " (outside your degree field)", "notes": notes}
    if deg_note:
        notes.append(deg_note)
    yrs = _years(req, verbatim=bool((req_text or "").strip()))
    if yrs >= r["max_years"]:
        return {"verdict": "reject", "reason": f"asks for {yrs}+ years of experience", "notes": notes}
    if yrs >= r["warn_years"]:
        notes.append(f"asks for {yrs}+ years")
    if verdict == "unknown":
        notes.append("location unclear")
    return {"verdict": "eligible", "reason": "", "notes": notes}


def log_reject(title, company, location, url, reason, source=""):
    p = C.rejects_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "date": C.today(),
             "title": C.one_line(title, 200), "company": C.one_line(company, 120), "location": C.one_line(location, 120),
             "link": (url or "")[:500], "reason": C.one_line(reason, 300), "source": source}
    try:
        lines = p.read_text().splitlines()[-300:]
    except OSError:
        lines = []
    for line in lines:
        try:
            old = json.loads(line)
        except ValueError:
            continue
        if old.get("reason") == entry["reason"] and old.get("title") == entry["title"] and old.get("company") == entry["company"]:
            return False          # already logged
    with p.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
    return True


def render(res):
    if res["verdict"] == "reject":
        return f"REJECT: {res['reason']}"
    n = f" ({'; '.join(res['notes'])})" if res["notes"] else ""
    if res["verdict"] == "pass-title":
        return f"PASS-TITLE: open the page{n}"
    return f"ELIGIBLE{n}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title")
    ap.add_argument("--company", default="")
    ap.add_argument("--location", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--text-file")
    ap.add_argument("--source", default="")
    ap.add_argument("--batch-file")
    ap.add_argument("--item", action="append", default=[], help="'title|company|location|url' (repeatable)")
    ap.add_argument("--log", action="store_true")
    a = ap.parse_args(argv)
    if a.batch_file or a.item:
        n_pass = n_rej = 0
        lines = list(a.item)
        if a.batch_file:
            lines += Path(a.batch_file).read_text().splitlines()
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.rsplit("|", 3)]
            while len(parts) < 4:
                parts.append("")
            title, company, location, url = parts[0], parts[1], parts[2], parts[3]
            res = evaluate(title, company, location)
            if res["verdict"] == "reject":
                n_rej += 1
                if a.log:
                    log_reject(title, company, location, url, res["reason"], a.source or "listing")
            else:
                n_pass += 1
            print(f"{i}. {render(res)} | {C.one_line(title, 80)}")
        print(f"SUMMARY: {n_pass} to open, {n_rej} rejected")
        return 0
    if not a.title:
        ap.error("--title or --batch-file is required")
    text = a.text
    if a.text_file:
        text = Path(a.text_file).read_text()[:60000]
    res = evaluate(a.title, a.company, a.location, text, a.source)
    if res["verdict"] == "reject" and a.log:
        log_reject(a.title, a.company, a.location, a.url, res["reason"], a.source)
    print(render(res))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as ex:  # keep the agent's terminal output short and clear
        print(f"jobs_gate error: {ex}", file=sys.stderr)
        sys.exit(1)
