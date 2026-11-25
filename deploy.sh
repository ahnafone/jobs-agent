#!/usr/bin/env bash
# Deploy project-side Hermes files into ~/.hermes (cron scripts must live under ~/.hermes/scripts, and
# Hermes only loads skills from ~/.hermes/skills). Safe to re-run. Does not touch secrets or config.yaml.
#   ./deploy.sh                 keep your edited job_filters.json / jobs_gate_rules.json
#   ./deploy.sh --force-filters overwrite both with the project copies
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
H="$HOME/.hermes"

mkdir -p "$H"; chmod 700 "$H"      # the board password hash and sessions live here
mkdir -p "$H/scripts" "$H/state" "$H/skills/productivity/job-tracker" "$H/cache/documents"
chmod 700 "$H/state"
for f in job_email_scan.py job_email_lookup.py jobs_common.py jobs_gate.py jobs_add.py jobs_mark.py \
         jobs_list.py jobs_export.py jobs_digest.py jobs_handover.py jobs_config.py resume_request.py resume_render.py; do
  install -m 755 "$SRC/scripts/$f" "$H/scripts/$f"
done
# data files: keep user edits unless forced, but always merge new subject keywords into job_filters.json
for f in job_filters.json jobs_gate_rules.json; do
  if [[ ! -f "$H/scripts/$f" || "${1:-}" == "--force-filters" ]]; then
    install -m 644 "$SRC/scripts/$f" "$H/scripts/$f"
  elif [[ "$f" == "job_filters.json" ]]; then
    python3 -I - "$SRC/scripts/$f" "$H/scripts/$f" <<'PYEOF'
import json, sys
src, dst = (json.load(open(p)) for p in sys.argv[1:3])
added = 0
for k, v in src.items():
    if isinstance(v, list) and isinstance(dst.get(k), list):
        new = [x for x in v if x not in dst[k]]
        dst[k] += new
        added += len(new)
if added:
    json.dump(dst, open(sys.argv[2], "w"), indent=2)
    print(f"merged {added} new entries into {sys.argv[2]}")
PYEOF
  fi
done
# Applications board (web app) next to the scripts it calls
mkdir -p "$H/board/static"
install -m 644 "$SRC/board/jobs_board.py" "$SRC/board/board_agent.py" "$SRC/board/board_auth.py" "$H/board/"
if [[ -d "$SRC/board/static" ]]; then
  rsync -a --delete "$SRC/board/static/" "$H/board/static/"
fi
# Personal settings (name, email, board address ...). Never overwritten; edit ~/.hermes/job-agent.json afterwards.
if [[ ! -f "$H/job-agent.json" ]]; then
  install -m 600 "$SRC/config/job-agent.example.json" "$H/job-agent.json"
  echo "NOTE: created $H/job-agent.json from the example. Edit it (name, email, resume roles ...) before first use."
fi
mkdir -p "$HOME/.config/systemd/user"
install -m 644 "$SRC/board/jobs-board.service" "$HOME/.config/systemd/user/jobs-board.service"
install -m 644 "$SRC/skills/job-tracker/SKILL.md" "$H/skills/productivity/job-tracker/SKILL.md"
systemctl --user daemon-reload 2>/dev/null || true
# A running board keeps the OLD code until restarted, and old code has no sign-in: restart it now.
if systemctl --user is-active --quiet jobs-board 2>/dev/null; then
  systemctl --user restart jobs-board && echo "restarted jobs-board"
fi
echo "deployed to $H"
if [[ ! -s "$H/board-auth.json" ]]; then
  echo "NOTE: the board has no password yet, so nobody (including you) can sign in."
  echo "      Run:  python3 $H/board/board_auth.py set-password"
fi
