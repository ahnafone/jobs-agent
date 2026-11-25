#!/usr/bin/env python3
"""Change a card's status by its 4-character code (the code resolution and the board commands live here).

  jobs_mark.py CODE --applied --by user [--note '...']        user said they applied
  jobs_mark.py CODE --applied --by receipt --note 'receipt <date>: <subject>'   an application-receipt email
  jobs_mark.py CODE --response interview|assessment|rejection|offer|info-request --note '<one line, deadline or ask>' [--date YYYY-MM-DD] [--deadline YYYY-MM-DD]
  jobs_mark.py CODE --archive --note 'duplicate of [XXXX]'     remove a duplicate or junk card from the board

Board actions (only with --actor you; these are the user's own clicks on the Applications board):
  jobs_mark.py CODE --actor you --stage new|applied|interview|offer|rejected|parked [--note '...']
  jobs_mark.py CODE --actor you --add-note 'text'
  jobs_mark.py CODE --actor you --review accepted|declined [--note 'reason']     Inbox decisions

`--applied` refuses to run without `--by user` or `--by receipt` (+ a note), so a card is never marked
applied on a guess. Responses are de-duplicated (same date + type) so a repeated email sweep adds nothing.
`--actor` says who wrote the change ("assistant" by default; the board passes "you"); it is stored as the
comment author, so the board can show who changed what and offer an undo.
"""
import argparse
import re
from datetime import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_common as C  # noqa: E402

STAGE_CHOICES = ("new", "applied", "interview", "offer", "rejected", "parked")
AUTHOR = {"you": "you", "assistant": "assistant"}


def comment(card, text, actor="assistant"):
    C.kanban("comment", card.id, "--author", AUTHOR.get(actor, "assistant"), text)


def apply_card(card, note="", actor="assistant"):
    if not card.applied or card.tag.upper() != "APPLIED":
        title = f"[APPLIED] {card.title} - {card.company}" if card.company else f"[APPLIED] {card.title}"
        C.kanban("edit", card.id, "--title", title)
    if card.status != "done":
        C.kanban("complete", card.id, "--summary", "Applied")
    comment(card, f"applied {C.today()}" + (f" - {C.one_line(note, 250)}" if note else ""), actor)


def set_stage(card, stage, note, actor):
    """Append a STAGE comment (undo is just another STAGE comment back). Applied moves also tag/complete the card."""
    prev = card.stage.lower()
    if stage == prev:
        return False
    if stage == "interview" and prev == "assessment":
        return False
    # a rejection never counts as an application unless the card was already applied
    if stage not in ("new", "parked", "rejected") and not (card.applied and card.tag.upper() == "APPLIED"):
        apply_card(card, f"marked by you on {C.today()}" if actor == "you" else (note or "applied"), actor)
    comment(card, f"STAGE {C.today()} {stage} from {prev}: {C.one_line(note, 200)}".rstrip(": "), actor)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("code")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--applied", action="store_true")
    g.add_argument("--response", choices=C.RESPONSE_TYPES)
    g.add_argument("--archive", action="store_true")
    g.add_argument("--stage", choices=STAGE_CHOICES)
    g.add_argument("--add-note")
    g.add_argument("--review", choices=("accepted", "declined", "pending"))
    ap.add_argument("--by", choices=("user", "receipt"))
    ap.add_argument("--actor", choices=tuple(AUTHOR), default="assistant")
    ap.add_argument("--note", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--deadline", default="")
    a = ap.parse_args(argv)

    cards = C.load_cards()
    found = C.resolve_code(cards, a.code)
    if not found:
        print(f"NOT-FOUND: no card with code {a.code.upper()}")
        return 0
    if len(found) > 1:
        print("AMBIGUOUS: " + ", ".join(f"[{c.code6}] {c.title} - {c.company}" for c in found) + " (use the 6-character code)")
        return 0
    card = found[0]
    label = f"[{card.code}] {card.title} - {card.company}"

    if (a.stage or a.add_note is not None or a.review) and a.actor != "you":
        print("ERROR: --stage, --add-note and --review are board actions and need --actor you", file=sys.stderr)
        return 2

    if a.stage:
        changed = set_stage(card, a.stage, a.note, a.actor)
        print(f"STAGE-SET {label}: {a.stage}" if changed else f"NO-CHANGE {label}: already {card.stage}")
    elif a.add_note is not None:
        text = C.clean(a.add_note, 1500)
        if not text:
            print("ERROR: the note is empty", file=sys.stderr)
            return 2
        comment(card, f"NOTE {C.today()}: {text}", a.actor)
        print(f"NOTE-ADDED {label}")
    elif a.review:
        comment(card, f"REVIEW {a.review}: {C.one_line(a.note, 200)}".rstrip(": "), a.actor)
        if a.review == "declined" and card.stage != "Parked":
            set_stage(card, "parked", a.note or "declined from the Inbox", a.actor)
        if a.review in ("accepted", "pending") and card.stage == "Parked":
            set_stage(card, "new", "back on the board", a.actor)
        print(f"REVIEW-{a.review.upper()} {label}")
    elif a.applied:
        if a.by is None:
            print("ERROR: --applied needs --by user or --by receipt", file=sys.stderr)
            return 2
        if a.by == "receipt" and not a.note.strip():
            print("ERROR: --by receipt needs --note with the receipt's date and subject", file=sys.stderr)
            return 2
        if card.applied and card.tag.upper() == "APPLIED":
            print(f"ALREADY-APPLIED {label}")
            return 0
        apply_card(card, a.note or "applied (told by user)", a.actor)
        print(f"MARKED-APPLIED {label}")
    elif a.response:
        date = a.date or C.today()
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise ValueError(date)
            datetime.strptime(date, "%Y-%m-%d")
            if a.deadline:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.deadline):
                    raise ValueError(a.deadline)
                datetime.strptime(a.deadline, "%Y-%m-%d")
        except ValueError:
            print("ERROR: --date and --deadline must be YYYY-MM-DD", file=sys.stderr)
            return 2
        if any(r["date"] == date and r["type"] == a.response for r in card.responses):
            print(f"ALREADY-RECORDED {label}: {date} {a.response}")
            return 0
        # an info-request (recruiter outreach) is not evidence of an application
        if a.response != "info-request" and not (card.applied and card.tag.upper() == "APPLIED"):
            apply_card(card, "applied (inferred from a response email)", a.actor)
        note = C.one_line(a.note, 280) or "see email"
        if a.deadline:
            note += f" [deadline {a.deadline}]"
        comment(card, f"RESPONSE {date} {a.response}: {note}", a.actor)
        print(f"RESPONSE-RECORDED {label}: {date} {a.response}")
    else:
        if not a.note.strip():
            print("ERROR: --archive needs --note with the reason", file=sys.stderr)
            return 2
        comment(card, f"archived: {C.one_line(a.note, 250)}", a.actor)
        C.kanban("archive", card.id)
        print(f"ARCHIVED {label}")
    try:
        import jobs_export
        jobs_export.safe_export()
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as ex:
        print(f"jobs_mark error: {ex}", file=sys.stderr)
        sys.exit(1)
