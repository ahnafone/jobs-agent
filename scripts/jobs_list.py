#!/usr/bin/env python3
"""List the job board. Codes, counts, dates and links come from here; paste the output verbatim.

  jobs_list.py                         open (not applied) cards, best fit first
  jobs_list.py --stage applied         Applied / Assessment / Interview / Offer / Rejected cards (any of: new applied
                                       assessment interview offer rejected all; comma-separated)
  jobs_list.py --since 7               cards added in the last 7 days
  jobs_list.py --recent                newest first (use for "what was posted most recently / what did I add lately")
  jobs_list.py --code 1C61 [3D07 ...]  full detail for specific cards (summary, why, comments)
  jobs_list.py --query xero            title / company / location search
  jobs_list.py --counts                one line of totals
  jobs_list.py --dupes                 groups of cards that look like the same job
  jobs_list.py --rejects [N]           jobs the gate turned away (title, reason, link)
  Options: --min-fit N  --exclude-source discord-link  --limit N (default 12)  --offset N  --json
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_common as C  # noqa: E402

APPLIED_STAGES = {"applied", "assessment", "interview", "offer", "rejected"}


def stage_filter(cards, spec):
    wanted = {s.strip().lower() for s in spec.split(",") if s.strip()}
    if not wanted or "open" in wanted:
        wanted = (wanted - {"open"}) | {"new"}
    if "applied" in wanted:
        wanted = (wanted - {"applied"}) | APPLIED_STAGES
    if "all" in wanted:
        return cards
    if "responses" in wanted:
        return [c for c in cards if c.responses]
    return [c for c in cards if c.stage.lower() in wanted]


def short(c):
    fit = f"{c.fit}" if c.fit is not None else "-"
    where = " · ".join(x for x in (c.location, c.pay) if x and x.lower() not in ("not stated", "unknown")) or "location/pay not stated"
    when = f"posted {c.posted}" if c.posted != "unknown" else "posted date unknown"
    extra = ""
    if c.applied and c.applied_date:
        extra = f" · applied {c.applied_date}"
    if c.last_response:
        lr = c.last_response
        extra += f" · {lr['type']} {lr['date']}"
    line1 = f"[{c.code}] {fit} · {c.stage} · {c.title} - {c.company}"
    line2 = f"    {where} · {when} · added {c.added}{extra}"
    line3 = f"    <{c.link}>" if c.link else "    (no link saved)"
    return "\n".join((line1, line2, line3))


def detail(c):
    out = [short(c), f"    source: {c.source or '?'} · received {c.received or c.added}"]
    if c.reason:
        out.append(f"    why: {c.reason}")
    if c.summary:
        out.append(f"    summary: {c.summary[:500]}")
    if c.action and c.action.lower() not in ("none", "none stated"):
        out.append(f"    ask/deadline: {c.action}")
    if c.research:
        out.append(f"    research: {c.research}")
    for r in c.responses:
        out.append(f"    response {r['date']} {r['type']}: {r['text']}")
    return "\n".join(out)


def counts_line(cards):
    n = {s: 0 for s in C.STAGES}
    for c in cards:
        n[c.stage] = n.get(c.stage, 0) + 1
    applied_total = sum(v for k, v in n.items() if k not in ("New", "Parked"))
    parts = [f"{applied_total} applied", f"{n['New']} open"]
    for k in ("Assessment", "Interview", "Offer", "Rejected"):
        if n[k]:
            parts.append(f"{n[k]} {k.lower()}")
    return " · ".join(parts)


def dupes(cards):
    groups, seen = [], set()
    for i, c in enumerate(cards):
        if c.id in seen:
            continue
        rest = [x for x in cards[i + 1:] if x.id not in seen]
        grp = [c]
        for x in rest:
            d, why = C.find_duplicate([c], x.link, x.company, x.title, x.location, [x.idempotency_key])
            if d is not None:
                grp.append(x)
                seen.add(x.id)
        if len(grp) > 1:
            groups.append(grp)
            seen.add(c.id)
    return groups


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default="open")
    ap.add_argument("--since", type=float)
    ap.add_argument("--recent", action="store_true")
    ap.add_argument("--code", nargs="+")
    ap.add_argument("--query")
    ap.add_argument("--counts", action="store_true")
    ap.add_argument("--dupes", action="store_true")
    ap.add_argument("--rejects", nargs="?", const=10, type=int)
    ap.add_argument("--min-fit", type=int, default=0)
    ap.add_argument("--exclude-source", default="")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.rejects is not None:
        import jobs_export
        rows = jobs_export.read_rejects(a.rejects)
        if not rows:
            print("no rejected jobs logged yet")
        for x in rows[: a.rejects]:
            print(f"{x.get('date','')} · {x.get('title','')} - {x.get('company','')} · {x.get('reason','')}\n    <{x.get('link','') or 'no link'}>")
        return 0

    cards = C.load_cards()
    if a.counts:
        print(counts_line(cards))
        return 0
    if a.dupes:
        gs = dupes(cards)
        if not gs:
            print("no duplicates found")
        for g in gs:
            print("DUPLICATE GROUP: " + " | ".join(f"[{c.code}] {c.stage} {c.title} - {c.company}" for c in g))
        return 0
    if a.code:
        out = []
        for code in a.code:
            found = C.resolve_code(cards, code)
            if not found:
                out.append(f"[{code.upper()}] not found")
            elif len(found) > 1:
                out.append(f"[{code.upper()}] ambiguous: " + ", ".join(f"[{c.code6}] {c.title}" for c in found))
            else:
                out.append(detail(found[0]))
        print("\n\n".join(out))
        return 0

    sel = stage_filter(cards, a.stage)
    if a.since is not None:
        cutoff = C.now_ts() - a.since * 86400
        sel = [c for c in sel if (c.created_at or 0) >= cutoff]
    if a.query:
        q = a.query.lower()
        sel = [c for c in sel if q in f"{c.title} {c.company} {c.location}".lower()]
    if a.min_fit:
        sel = [c for c in sel if c.fit is None or c.fit >= a.min_fit]
    if a.exclude_source:
        sel = [c for c in sel if c.source != a.exclude_source]
    if a.recent:
        sel.sort(key=lambda c: c.created_at or 0, reverse=True)
    elif a.stage.lower().strip() in ("open", "new"):
        sel.sort(key=lambda c: (-(c.fit if c.fit is not None else -1), -(c.created_at or 0)))
    else:
        sel.sort(key=lambda c: c.created_at or 0, reverse=True)

    total = len(sel)
    page = sel[a.offset: a.offset + a.limit]
    if a.json:
        print(json.dumps([{k: v for k, v in c.items() if k not in ("body",)} for c in page], indent=1, default=str))
        return 0
    if not page:
        print("no matching cards")
        print(counts_line(cards))
        return 0
    print("\n".join(short(c) for c in page))
    end = a.offset + len(page)
    print(f"-- showing {a.offset + 1}-{end} of {total} · {counts_line(cards)}")
    if end < total:
        print(f"-- more: add --offset {end}")
    print("-- full board with filters and sorting: file://" + str(C.export_dir() / "jobs-board.html").replace(" ", "%20"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as ex:
        print(f"jobs_list error: {ex}", file=sys.stderr)
        sys.exit(1)
