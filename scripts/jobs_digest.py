#!/usr/bin/env python3
"""Daily digest, built entirely in code (no model): runs as a `--no-agent` cron job, stdout is delivered to Discord.

  jobs_digest.py             print the digest (nothing at all when there is nothing new) and remember the time
  jobs_digest.py --dry-run   print without remembering

Contents: responses since the last digest, then new alerts since the last digest (fit >= MIN_FIT, never links
the user sent themselves, never applied cards), then counts. Also refreshes jobs-board.html / applications.csv.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_common as C  # noqa: E402
import jobs_config as CFG  # noqa: E402

MIN_FIT = 40
MAX_CHARS = 1850
STATE = "job_digest_state.json"


def load_last():
    try:
        return int(json.loads((C.state_dir() / STATE).read_text()).get("last_ts", 0))
    except (OSError, ValueError, TypeError):
        return 0


def save_last(ts):
    d = C.state_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{STATE}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps({"last_ts": ts}))
    os.replace(tmp, d / STATE)


def build(cards, last_ts):
    resp, new = [], []
    for c in cards:
        for r in c.responses:
            if r["ts"] > last_ts:
                resp.append((c, r))
        if (not c.applied and c.source not in ("discord-link", "board") and (c.created_at or 0) > last_ts
                and (c.fit if c.fit is not None else 0) >= MIN_FIT):
            new.append(c)
    new.sort(key=lambda c: -(c.fit or 0))
    resp.sort(key=lambda x: x[1]["ts"])
    return resp, new


def main(argv=None):
    dry = "--dry-run" in (argv if argv is not None else sys.argv[1:])
    now = C.now_ts()
    last = load_last() or now - 86400
    cards = C.load_cards()
    resp, new = build(cards, last)
    try:
        import jobs_export
        jobs_export.safe_export()
    except Exception:  # noqa: BLE001
        pass
    if not resp and not new:
        if not dry:
            save_last(now)
        return 0
    lines = [f"**Job digest {C.today()}**"]
    if resp:
        lines.append("**Responses**")
        for c, r in resp:
            line = f"• [{c.code}] {r['type']} - {c.title} at {c.company}: {r['text'][:150]}"
            if sum(len(x) + 1 for x in lines) + len(line) > MAX_CHARS // 2:
                lines.append("…more replies: ask me for email updates")
                break
            lines.append(line)
    shown = 0
    if new:
        lines.append(f"**New (fit {MIN_FIT}+)**")
        for c in new:
            block = [f"• [{c.code}] {c.fit} - {c.title} - {c.company} · {c.location or 'location not stated'}"
                     + (f" · {c.pay}" if c.pay and c.pay.lower() != "not stated" else "")
                     + (f" · posted {c.posted}" if c.posted != "unknown" else ""),
                     f"  <{c.link}>" if c.link else "  (no link)"]
            if sum(len(x) + 1 for x in lines + block) > MAX_CHARS - 220:
                break
            lines += block
            shown += 1
        if shown < len(new):
            lines.append(f"…and {len(new) - shown} more on the board")
    n_applied = sum(1 for c in cards if c.applied)
    n_inbox = sum(1 for c in cards if c.review == "pending")
    n_open = sum(1 for c in cards if not c.applied and c.stage != "Parked" and c.review != "pending")
    url = CFG.board_url()
    lines.append(f"{n_applied} applied · {n_open} open" + (f" · {n_inbox} in your Inbox" if n_inbox else "") + f" · board: {url}")
    lines.append('Say "apply to CODE" for a handover prompt.')
    text = "\n".join(lines)
    if len(text) > 1990:
        text = text[:1985].rstrip() + "\n…"
    print(text)
    if not dry:
        save_last(now)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as ex:
        print(f"jobs_digest error: {ex}", file=sys.stderr)
        sys.exit(1)
