# jobs-agent

A personal job-search tracker built on the Hermes agent runtime: an assistant that watches your
inbox for job emails, checks every posting against your eligibility rules, keeps the survivors on a kanban board, and
writes a tailored one-page resume and cover letter on request. A small web app (the **Applications board**) is the
front end. Everything runs on your own machine; secrets are never stored in this repo.

**Live demo:** once Pages is enabled it is at `https://<your-github-user>.github.io/jobs-agent/` (made-up companies; nothing leaves your browser).

```
email ──► job_email_scan.py ─► Hermes cron (job-intake) ─► jobs_add.py ─► gate ─► kanban "jobs" board
                                                                                   │
              Applications board (web) ◄── jobs_board.py ◄─────────────────────────┘
                     │  paste a link / chat / "write my resume"
                     └─► board_agent.py ─► Hermes (read-only web) ─► resume_request.py ─► resume_render.py ─► PDF + DOCX
```

## Quick start

1. Install Hermes, create a kanban board named `jobs`, and put your own address in `~/.config/himalaya/config.toml`
   (see `config/himalaya.config.toml.example`).
2. `./deploy.sh` copies the scripts, skill and board into `~/.hermes` and creates `~/.hermes/job-agent.json` from
   `config/job-agent.example.json` if you don't have one. **Edit that file**: your name, email, board address,
   writer rules and the roles your resume templates use. It is never overwritten and is git-ignored if you keep a copy here.
3. Put your facts in `profile.md` (and `odd-jobs-profile.md` if you use the second template) and your Word templates
   in `Templates/`, both inside `export_dir` (default `~/Documents/Job Applications`). Template layout:
   `resume-writer/README.md`.
4. Edit `scripts/jobs_gate_rules.json`. The shipped file is a made-up **example** (computer-science graduate, one
   metro area, eligible to work in Canada); replace the places, degree fields, seniority and citizenship rules with yours.
5. Start the board and set a password (see "Run it" below).

### Settings (`~/.hermes/job-agent.json`)
| Key | Meaning |
| --- | --- |
| `name`, `email` | Who the resume is for; `email` is the `mailto:` link every generated PDF must contain |
| `board_url`, `allowed_hosts` | Address shown in the daily digest; Host headers the board accepts |
| `export_dir`, `timezone` | Where profile, templates and output live; the timezone used for "today" |
| `intake_job_id` | Id of the Hermes cron job the board's "Check my email now" button runs |
| `seeker_profile` | One paragraph the link-reading model uses to score fit |
| `resume.*` | Summary phrases, the roles in your templates, skills the writer must never claim, extra writer rules |

Environment variables (`JOBS_EXPORT_DIR`, `JOBS_TZ`, `BOARD_URL`, `BOARD_ALLOWED_HOSTS`, `BOARD_INTAKE_JOB_ID`,
`JOBS_AGENT_CONFIG`) override the file.

## Demo
`python3 demo/build.py` writes a static copy of the board to `docs/` that answers every `/api/*` call from
`demo/fixtures.json` in the browser (`demo/mock-api.js`). The Pages workflow builds and publishes it on every push to
`main` (Settings → Pages → Source: GitHub Actions). `python3 demo/make_fixtures.py` regenerates the sample data from the
real board code and a throwaway database.

## Reference

| What | Where |
| --- | --- |
| Model | `~/.hermes/config.yaml`: default provider `anthropic`, `claude-haiku-5-5`, `reasoning_effort: high`. `#jobs` is overridden to `deepseek-v4.1-flash` (opencode-go) via `platforms.discord.channel_overrides` |
| Chat (optional) | a Discord bot and a mention-free `#jobs` channel with a channel prompt, set in Hermes' own config |
| Email scan | `scripts/job_email_scan.py` (read-only, newest 100, prefilter), filters in `scripts/job_filters.json` |
| Skill | `skills/job-tracker/SKILL.md` (small; the model calls helper scripts instead of computing codes, keys, dates, counts) |
| Helpers | `scripts/jobs_*.py` (see below) and `scripts/job_email_lookup.py` |
| Tracker view | `~/Documents/Job Applications/jobs-board.html` and `applications.csv`, rebuilt on every change |
| Cron | `prompts/job-intake.md` (model); the daily digest is `scripts/jobs_digest.py` (no model) |
| Storage | kanban board `jobs` (cards created `blocked`, deduped by idempotency key) |
| Browser | `browser.headed: true`, persistent profile `~/.hermes/browser-profile-jobs` |

## Helpers (run as `python3 ~/.hermes/scripts/<name>.py`)
| Script | Purpose |
| --- | --- |
| `jobs_gate.py` | Eligibility gate (title, location, degree field, years, citizenship, clearance, licence, US-only). Rules in `jobs_gate_rules.json` |
| `jobs_add.py` | Create alert/applied cards (`--review` puts bot finds in the board Inbox): gate, dedupe (key, URL, requisition id, company+title), received date, board commands |
| `jobs_mark.py` | Mark applied (needs `--by user` or a receipt note), record responses, archive. `--actor you` is for board clicks: `--stage`, `--add-note`, `--review` |
| `jobs_list.py` | Cards with code, fit, stage, dates, link; `--counts`, `--dupes`, `--rejects`, `--code` |
| `job_email_lookup.py` | Read-only on-demand email check for one company, or `--sweep` |
| `jobs_export.py` | Rebuild the HTML board and CSV |
| `jobs_digest.py` | No-LLM daily digest (silent when nothing is new) |
| `jobs_handover.py` | The "apply to this job" prompt, built without a model |

## Applications board (web)
`board/jobs_board.py` (standard library only) serves `board/static/` and a small JSON API on `127.0.0.1:8765`.
It reads the kanban board read-only and changes it only through the same `jobs_*.py` scripts the bot uses, so the
eligibility gate, duplicate checks and "never mark applied on a guess" rules stay in one place. Board clicks are
stored as kanban comments authored `you` (`STAGE`, `NOTE`, `REVIEW`), so the activity log and undo need no extra
database. Jobs the bot finds go to the **Inbox** (`jobs_add.py --review`); links you paste skip it. Saved views and
dismissed nudges live in `~/.hermes/state/board_meta.json`.

**Messages (resume and cover letter chat).** The top bar's **Messages** button opens a full-page screen at `#/messages`
(one chat per job at `#/messages/<CODE>`; the card popup only has an **Open chat** button). Opening a job's chat for the
first time starts the draft; the agent posts its progress, any questions and the finished files, and you ask for
changes in the thread. Each file card has Open / PDF / Word and a preview pane (Resume or Cover letter, with the saved
path and a copy button). Every run saves a version; **Undo that change** after a revision re-renders the earlier version
as a new one. **Approve** only saves the files; the agent never applies to a job. **Stop** ends a run in progress. The
badge on the button counts agent messages you haven't seen. API: `GET /api/messages`,
`GET /api/cards/<CODE>/resume/chat`, `POST /api/cards/<CODE>/resume/read`, and `POST /api/cards/<CODE>/resume` with
`{message}`, `{undo}`, `{stop}`, `{approve}` or `{answers}`. The conversation lives on the server in
`~/.hermes/state/resume_requests/<CODE>.chat.json`, so it survives a reload; `<CODE>.read` is the board's read marker.

Link reading and chat use `hermes chat --oneshot` with `deepseek-v4.1-flash` (opencode-go) and only the `web` and
`browser` toolsets (no terminal, no email sending). Everything it returns is re-checked by the server (gate, dedupe,
validated fields). Email questions use the read-only `job_email_lookup.py`, run by the server, never by the model.

Safety: localhost only; sign-in required for every `/api` call; Host header allow-list; every change needs an `X-Board: 1` header and a same-site Origin;
CSP `script-src 'self'`; pasted links must be public http(s) (no localhost / private addresses); no email text is
ever sent to the browser.

**Run it (user service)**
```
./deploy.sh
systemctl --user daemon-reload && systemctl --user enable --now jobs-board.service
curl -s -H 'Host: localhost' localhost:8765/api/session      # {"authed": false} until you sign in
```

**Sign-in** - every `/api` request needs a signed-in browser; the page itself (no data) loads for anyone. One account,
password only. Set it (and change it later) on this computer, in a terminal, so it never goes through chat:
```
python3 ~/.hermes/board/board_auth.py set-password     # 12+ characters; signs every browser out
python3 ~/.hermes/board/board_auth.py sign-out-all     # end every session, keep the password
python3 ~/.hermes/board/board_auth.py status
```
- Only a salted scrypt hash is stored (`~/.hermes/board-auth.json`, mode 600). Sessions last 30 days and renew when
  used (`~/.hermes/board-sessions.json`, mode 600, holds only a hash of each token). The cookie is HttpOnly,
  SameSite=Strict, and Secure + `__Host-` on the public address. "Sign out" is the door icon in the top bar.
- Wrong passwords: after 5 in a row from one address, it must wait 1, then 5, 15, 60 minutes. 30 wrong tries in an hour
  (all addresses) pauses sign-in for everyone until the hour is up; signed-in browsers are unaffected. To unlock
  yourself at once: `systemctl --user restart jobs-board` (the counters live in memory). Failed sign-ins are logged to the journal (`journalctl --user -u jobs-board | grep "sign-in failed"`),
  never the password.
- If you forget the password, run `set-password` again; the file is just replaced.
- `./deploy.sh` restarts the board when it is running (old code has no sign-in), so deploy, then set a password.
- A password change ends every session, even one that was being renewed at that moment.
- Upgrading from an older board version signs every browser out once; sign in again.

**Put it on the internet (Cloudflare tunnel, free, no Zero Trust needed)** - only after a password is set.
1. Add the hostname to the tunnel in `/etc/cloudflared/config.yml` above the 404 line (needs sudo):
   ```
   - hostname: board.example.com
     service: http://127.0.0.1:8765
   ```
   then `sudo systemctl restart cloudflared` and `cloudflared tunnel route dns <your-tunnel> board.example.com`.
2. Open the address in a private window: you should see the Sign in page, not the board. Check that
   `curl -s https://board.example.com/api/state` answers `401`.
3. Add the hostname to `allowed_hosts` in `~/.hermes/job-agent.json` (or set `BOARD_ALLOWED_HOSTS`, comma separated).
4. Do not expose the port any other way.

## Deploy
`./deploy.sh` copies the scripts, filters and skill into `~/.hermes`. Your edits to `job_filters.json` and
`jobs_gate_rules.json` are kept unless `--force-filters`.

## Secrets (outside this folder)
- Discord token: `~/.hermes/.env` (`DISCORD_BOT_TOKEN`), mode 600.
- Gmail app password: `~/.hermes/secrets/gmail_app_password`, mode 600, read by himalaya via `password.command`.

## Email setup
1. Copy `config/himalaya.config.toml.example` to `~/.config/himalaya/config.toml` and set your address.
2. `himalaya account check`, then `himalaya envelope list --page-size 5` to verify.
3. Dry run: `python3 ~/.hermes/scripts/job_email_scan.py`.

## Cron jobs
```
hermes cron create "every 45m" --name job-intake --script job_email_scan.py --deliver discord \
  --reasoning-effort high --skill job-tracker "$(cat prompts/job-intake.md)"
hermes cron create "0 12 * * *" --name job-digest --deliver discord --timezone America/Vancouver \
  --no-agent --script jobs_digest.py
```

## Privacy design
Only the newest 100 envelopes are listed. Only emails whose sender address domain or subject match have bodies
fetched (messages are read without `--seen`). Unmatched mail never reaches the model, logs or storage. The only
email state is an integer marker (highest processed IMAP UID, plus a pending value) in
`~/.hermes/state/job_email_state.json`. The marker advances only when the intake prompt runs
`job_email_scan.py --commit`, so a failed run is retried (kanban idempotency keys prevent duplicate cards).

## Tests
CI runs the same files on every push. `python3 -I tests/test_jobs_helpers.py` (helpers, gate, dedupe, export escaping, email lookup privacy) and
`python3 -I tests/test_board.py` (the web server against a temp board: security checks, inbox, stages, undo, link
intake, chat, calendar, merge) and
`python3 -I tests/test_job_email_scan.py` (fake himalaya, temp HOME; covers injection, spoofed domains, caps,
commit/retry, error rate limiting), `python3 -I tests/test_board_auth.py` (sign-in, sessions, lockouts) and
`python3 -I tests/test_jobs_config.py` (settings loader and demo build guards) and `python3 -I tests/test_resume_helpers.py` (resume writer; needs `python-docx`, LibreOffice and poppler; builds its own
synthetic templates). All tests use a temp HOME, fake `hermes`/`himalaya` and invented people, and never touch a real board.

## Browser safety
The persistent browser profile may hold logged-in job-site sessions. Only log in to job sites in it (never
Gmail/Google). The skill forbids clicking, submitting, logging in or opening action links during research.
