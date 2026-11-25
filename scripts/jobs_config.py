#!/usr/bin/env python3
"""Per-user settings for the jobs-agent scripts, read from one JSON file so no personal detail lives in the code.

File: $JOBS_AGENT_CONFIG, else ~/.hermes/job-agent.json. Copy config/job-agent.example.json there and edit it.
A missing file is fine: every key has a neutral default. Keys you leave out keep their default; the nested
"resume" block is merged key by key. The file is re-read when it changes, so most edits apply to a running server;
the board reads allowed_hosts and intake_job_id once at start, so restart it (systemctl --user restart jobs-board)
after changing those. A broken file or a wrong-typed value prints one warning to stderr and falls back to the default.

Environment variables that already existed (JOBS_EXPORT_DIR, JOBS_TZ, BOARD_URL, BOARD_ALLOWED_HOSTS,
BOARD_INTAKE_JOB_ID) still win over the file.
"""
import copy
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

DEFAULTS = {
    "name": "Your Name",                       # used in prompts, resume and letter file names
    "email": "you@example.com",                # the mailto link every generated PDF must carry
    "board_url": "http://127.0.0.1:8765",      # where the board is reachable (shown in the daily digest)
    "allowed_hosts": ["localhost", "127.0.0.1"],   # Host headers the board accepts; add your public hostname
    "export_dir": "~/Documents/Job Applications",  # profile.md, Templates/, Resumes/, Cover Letters/, jobs-board.html
    "timezone": "UTC",                         # IANA name, used for "today" in the board, digest and cards
    "intake_job_id": "",                       # id of the Hermes cron job the board's "check email" button triggers
    "seeker_profile": (                        # one paragraph the board's link-reading model uses to score fit
        "The job seeker: describe your location, education, years of experience, the roles you want, "
        "your pay expectations and seniority range here (config key seeker_profile)."
    ),
    "resume": {
        "years_phrase": "5+ years of experience",   # must appear (bold) in every resume summary
        "employer_phrase": "",                      # e.g. your own company name; must appear in the summary. "" = off
        "profile_files": ["profile.md", "odd-jobs-profile.md"],   # facts the writer may use, in export_dir
        "degree_rewrite": ["", ""],                 # [from, to]: fix a wording in the templates, e.g. a degree name
        "banned_claims": [],                        # [[regex, label], ...] skills you must never claim
        "software_roles": [],                       # [[company, location, dates], ...] in template order
        "odd_jobs_roles": [],                       # ["Role, Company, City", ...] in template order
        "writer_notes": [],                         # extra hard rules appended to the writer prompt, one string each
    },
}


def config_path():
    return Path(os.environ.get("JOBS_AGENT_CONFIG") or Path.home() / ".hermes" / "job-agent.json")


_cache = {"key": None, "value": None}


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if k.startswith("_"):
            continue                           # "_comment" keys
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


NON_EMPTY = ("name", "email", "board_url", "export_dir", "timezone")


def _warn(msg):
    sys.stderr.write(f"jobs_config: {msg}\n")


def _is_pairs(v, n):
    return isinstance(v, list) and all(isinstance(x, (list, tuple)) and len(x) == n and all(isinstance(y, str) for y in x) for x in v)


def _validate(value):
    """Replace wrong-typed values with the defaults (one warning each) so no caller has to defend against them."""
    def bad(path, why):
        _warn(f"ignoring {path}: {why}; using the default")

    res = value["resume"]
    for key in ("name", "email", "board_url", "export_dir", "timezone", "intake_job_id", "seeker_profile"):
        if not isinstance(value.get(key), str) or (key in NON_EMPTY and not value[key].strip()):
            bad(key, "expected non-empty text")
            value[key] = DEFAULTS[key]
    if not isinstance(value.get("allowed_hosts"), list) or not all(isinstance(h, str) for h in value["allowed_hosts"]):
        bad("allowed_hosts", "expected a list of host names")
        value["allowed_hosts"] = list(DEFAULTS["allowed_hosts"])
    if not isinstance(res, dict):
        bad("resume", "expected an object")
        value["resume"] = res = copy.deepcopy(DEFAULTS["resume"])
    for key in ("years_phrase", "employer_phrase"):
        if not isinstance(res.get(key), str):
            bad(f"resume.{key}", "expected text")
            res[key] = DEFAULTS["resume"][key]
    if not res["years_phrase"].strip():
        bad("resume.years_phrase", "must not be empty")
        res["years_phrase"] = DEFAULTS["resume"]["years_phrase"]
    if not isinstance(res.get("profile_files"), list) or not all(isinstance(x, str) for x in res["profile_files"]):
        bad("resume.profile_files", "expected a list of file names")
        res["profile_files"] = list(DEFAULTS["resume"]["profile_files"])
    if not _is_pairs([res.get("degree_rewrite")], 2):
        bad("resume.degree_rewrite", 'expected ["from", "to"]')
        res["degree_rewrite"] = list(DEFAULTS["resume"]["degree_rewrite"])
    if not _is_pairs(res.get("banned_claims"), 2):
        bad("resume.banned_claims", 'expected [["regex", "label"], ...]')
        res["banned_claims"] = []
    else:
        keep = []
        for pat, label in res["banned_claims"]:
            try:
                re.compile(pat)
                keep.append([pat, label])
            except re.error as e:
                bad(f"resume.banned_claims pattern {pat!r}", f"not a valid regex ({e})")
        res["banned_claims"] = keep
    if not _is_pairs(res.get("software_roles"), 3):
        bad("resume.software_roles", 'expected [["company", "location", "dates"], ...]')
        res["software_roles"] = []
    for key in ("odd_jobs_roles", "writer_notes"):
        if not isinstance(res.get(key), list) or not all(isinstance(x, str) for x in res[key]):
            bad(f"resume.{key}", "expected a list of text")
            res[key] = []
    return value


def load():
    """The merged settings (defaults + file). Never raises: a missing or broken file gives the defaults."""
    p = config_path()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        key = (str(p), None, None)
    if _cache["key"] == key and _cache["value"] is not None:
        return _cache["value"]
    data = {}
    if key[1] is not None:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
            else:
                _warn(f"{p} must contain a JSON object; using the defaults")
        except (OSError, ValueError) as e:
            _warn(f"could not read {p} ({e}); using the defaults")
    value = _validate(_merge(DEFAULTS, data))
    _cache["key"], _cache["value"] = key, value
    return value


def is_placeholder():
    """True while the name and email are still the built-in placeholders (nothing has been configured)."""
    c = load()
    return c["name"] == DEFAULTS["name"] or c["email"] == DEFAULTS["email"]


def get(key, default=None):
    return load().get(key, default)


def resume():
    return load()["resume"]


def name_slug():
    """'Jane Doe' -> 'Jane-Doe' (the part of the resume and letter file names that identifies the person)."""
    plain = unicodedata.normalize("NFKD", str(get("name") or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", plain).strip("-")
    return s or "Applicant"


def allowed_hosts():
    env = (os.environ.get("BOARD_ALLOWED_HOSTS") or "").strip()
    hosts = env.split(",") if env else get("allowed_hosts") or []
    return {h.strip().lower() for h in hosts if str(h).strip()}


def board_url():
    return os.environ.get("BOARD_URL") or get("board_url")


def intake_job_id():
    return (os.environ.get("BOARD_INTAKE_JOB_ID") or get("intake_job_id") or "").strip()


def export_dir():
    return Path(os.environ.get("JOBS_EXPORT_DIR") or os.path.expanduser(get("export_dir"))).expanduser()


def timezone_name():
    return os.environ.get("JOBS_TZ") or get("timezone")
