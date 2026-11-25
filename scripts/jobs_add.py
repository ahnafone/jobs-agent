#!/usr/bin/env python3
"""Create a job card on the `jobs` board with the gate, dedupe, key and body all handled in code.

Usage (every value in SINGLE quotes; the shell never sees them as code):
  jobs_add.py --title 'Software Developer' --company 'Acme' --location 'Surrey, BC' --fit 72 \
      --reason 'why it fits' --source discord-link --link 'https://...' \
      [--pay '$70-80K'] [--schedule 'Full-time'] [--posted 2026-11-20 | --posted-ago '3 days ago'] \
      [--requirements 'education / years / citizenship / licence lines copied verbatim from the posting'] \
      [--summary '...'] [--description '...'] [--action 'deadline or ask'] [--research ok|'blocked (reason)'|'skipped (budget)'] \
      [--force]                       # add even if the gate rejects (only when the user said "add it anyway")
  (add --review for jobs you found yourself: the card waits in the user's Inbox until they accept it)
  jobs_add.py --applied --company X --title Y [--link URL] [--note 'receipt 2026-11-20: subject']

Prints exactly one outcome line:
  CREATED [CODE] ...      REJECTED: <reason>      ALREADY-TRACKED [CODE] ...      ALREADY-APPLIED [CODE] ...
  MARKED-APPLIED [CODE] ...
The code, key, received date, board commands and duplicate checks are computed here; never by the model.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_common as C  # noqa: E402
import jobs_gate as G  # noqa: E402

SOURCES = ("email", "discord", "discord-link", "board")


def make_body(a, posted, gate_notes):
    lines = [f"Fit: {a.fit}/100 - {C.one_line(a.reason, 400)}"]
    co = f"Company: {C.one_line(a.company, 120)} | Location: {C.one_line(a.location, 120) or 'not stated'} | Pay: {C.one_line(a.pay, 120) or 'not stated'}"
    if a.schedule:
        co += f" | Schedule: {C.one_line(a.schedule, 60)}"
    lines += [co,
              f"Source: {a.source}, received {C.today()}",
              f"Posted: {posted}",
              f"Link: {a.link or 'none'}",
              f"Action requested of user / deadline: {C.one_line(a.action, 200) or 'none stated'}",
              f"Summary: {C.one_line(a.summary, 700)}"]
    if a.requirements:
        lines.append(f"Requirements: {C.one_line(a.requirements, 700)}")
    if a.description:
        lines.append(f"Description: {C.one_line(a.description, 1300)}")
    lines.append(f"Research: {C.one_line(a.research, 80) or 'ok'}")
    if gate_notes:
        lines.append(f"Gate: passed ({'; '.join(gate_notes)})")
    return "\n".join(l for l in lines if "\x00" not in l) + "\n"


def mark_applied_card(card, note):
    from jobs_mark import apply_card  # local import: avoids a cycle at import time
    apply_card(card, note)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True)
    ap.add_argument("--company", required=True)
    ap.add_argument("--location", default="")
    ap.add_argument("--pay", default="")
    ap.add_argument("--schedule", default="")
    ap.add_argument("--fit", type=int)
    ap.add_argument("--reason", default="")
    ap.add_argument("--source", default="discord-link", choices=SOURCES)
    ap.add_argument("--link", default="")
    ap.add_argument("--posted", default="")
    ap.add_argument("--posted-ago", default="")
    ap.add_argument("--requirements", default="")
    ap.add_argument("--summary", default="")
    ap.add_argument("--description", default="")
    ap.add_argument("--action", default="")
    ap.add_argument("--research", default="ok")
    ap.add_argument("--applied", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--review", action="store_true",
                    help="put the new card in the user's Inbox to accept or decline (use for jobs the assistant found, "
                         "not for links the user sent)")
    a = ap.parse_args(argv)

    a.link = a.link.strip()
    a.title, a.company = C.one_line(a.title, 200), C.one_line(a.company, 120)
    if not a.title or not a.company:
        print("ERROR: --title and --company must not be empty", file=sys.stderr)
        return 2
    if a.link and (re.search(r"[\s\x00-\x1f\x7f]", a.link.strip()) or not C.norm_url(a.link)):
        print("ERROR: --link must be a public http(s) URL (or omit it)", file=sys.stderr)
        return 2
    cards = C.load_cards()

    # ---- applied cards: receipt or the user's word, no research, no gate ----
    if a.applied:
        keys = [C.applied_key(a.company, a.title), C.url_key(a.link)]
        dup, why = C.find_duplicate(cards, a.link, a.company, a.title, a.location, keys)
        if dup is None:
            # same company, any similar title already tracked? (title text may differ between receipt and alert)
            for c in cards:
                if C.company_norm(c.company) == C.company_norm(a.company) and C.alnum(c.title) == C.alnum(a.title):
                    dup = c
                    break
        if dup is not None:
            if dup.applied:
                print(f"ALREADY-APPLIED [{dup.code}] {dup.title} - {dup.company}")
                return 0
            mark_applied_card(dup, a.note or "applied")
            print(f"MARKED-APPLIED [{dup.code}] {dup.title} - {dup.company}")
            _refresh()
            return 0
        for c in C.load_cards(include_archived=True):
            if c.status == "archived" and c.idempotency_key in [k for k in keys if k]:
                print(f"ALREADY-ARCHIVED [{c.code}] {c.title} - {c.company} (removed from the board earlier; ask the user)")
                return 0
        similar = [c for c in cards if not c.applied and C.company_norm(c.company) == C.company_norm(a.company)]
        for c in similar[:3]:
            print(f"POSSIBLE-MATCH [{c.code}] {c.title} - {c.company} (an open card at the same company; ask the user "
                  f"whether it is the same job. If yes: jobs_mark.py {c.code} --applied --by receipt --note '...')")
        title = f"[APPLIED] {a.title} - {a.company}"
        body = f"Applied: {a.company}, {a.title}, {C.today()}. Receipt only; no research.\nLink: {a.link or 'none'}\n"
        out = C.kanban("create", title, "--idempotency-key", keys[0], "--initial-status", "blocked",
                       "--created-by", "job-tracker", "--body-file", "-", input_text=body)
        m = re.search(r"\bt_[0-9a-f]{8}\b", out)
        if not m:
            print(f"ERROR: could not read the new card id from: {out[:200]}", file=sys.stderr)
            return 1
        C.kanban("complete", m.group(0), "--summary", "Applied; receipt recorded.")
        C.kanban("comment", m.group(0), f"applied {C.today()}" + (f" - {C.one_line(a.note, 250)}" if a.note else ""))
        print(f"CREATED [{m.group(0)[-4:].upper()}] [APPLIED] {a.title} - {a.company}")
        _refresh()
        return 0

    # ---- alert cards ----
    if a.fit is None or not 0 <= a.fit <= 100:
        print("ERROR: --fit must be an integer 0-100", file=sys.stderr)
        return 2
    if not a.reason:
        print("ERROR: --reason is required (one or two sentences on why this score)", file=sys.stderr)
        return 2

    keys = [C.url_key(a.link)] if C.link_has_id(a.link) else [C.fallback_key(a.company, a.title, a.location)]
    dup, why = C.find_duplicate(cards, a.link, a.company, a.title, a.location, keys)
    if dup is not None:
        kind = "ALREADY-APPLIED" if dup.applied else "ALREADY-TRACKED"
        print(f"{kind} [{dup.code}] {dup.title} - {dup.company} ({why})")
        return 0

    for c in C.load_cards(include_archived=True):
        if c.status == "archived" and c.idempotency_key in keys:
            print(f"ALREADY-ARCHIVED [{c.code}] {c.title} - {c.company} (this job was removed from the board earlier; "
                  "ask the user before re-adding it)")
            return 0
    gate_text = "\n".join(x for x in (a.requirements, a.description, a.summary) if x)
    res = G.evaluate(a.title, a.company, a.location, gate_text or "", a.source, req_text=a.requirements)
    if res["verdict"] == "reject" and not a.force:
        G.log_reject(a.title, a.company, a.location, a.link, res["reason"], a.source)
        print(f"REJECTED: {res['reason']}")
        _refresh()
        return 0

    posted = C.norm_posted(a.posted or a.posted_ago)
    body = make_body(a, posted, res["notes"] + (["forced: " + res["reason"]] if res["verdict"] == "reject" else []))
    title = f"[{a.fit}] {a.title} - {a.company}"
    out = C.kanban("create", title, "--idempotency-key", keys[0], "--initial-status", "blocked",
                   "--created-by", "job-tracker", "--body-file", "-", input_text=body)
    m = re.search(r"\bt_[0-9a-f]{8}\b", out)
    if not m:
        print(f"ERROR: could not read the new card id from: {out[:200]}", file=sys.stderr)
        return 1
    code = m.group(0)[-4:].upper()
    if a.review:
        C.kanban("comment", m.group(0), "--author", "assistant", f"REVIEW pending: found by {a.source} on {C.today()}")
    extra = f" - posted {posted}" if posted != "unknown" else " - posted date unknown"
    print(f"CREATED [{code}] fit {a.fit}: {a.title} - {a.company}{extra} | source {a.source} | {a.link or 'no link'}" + (" | in Inbox" if a.review else ""))
    _refresh()
    return 0


def _refresh():
    try:
        import jobs_export
        jobs_export.safe_export()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as ex:
        print(f"jobs_add error: {ex}", file=sys.stderr)
        sys.exit(1)
