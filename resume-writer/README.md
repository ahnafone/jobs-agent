# resume-writer

A Hermes profile plus two scripts that write a tailored one-page resume and cover letter for one job card.

- Profile: `resume-writer` (an Anthropic model; the author uses `claude-haiku-5-5`). Its only toolset is `web` (read-only lookup). It has no file, terminal, email or chat access. Its instructions are in `~/.hermes/profiles/resume-writer/SOUL.md`; start from `resume-writer/SOUL.example.md` in this repo.
- `scripts/resume_request.py`: entry point. Reads the job card, builds the writer prompt, calls the profile, checks the JSON it returns, and tracks the request.
- `scripts/resume_render.py`: fills the Word templates from a JSON spec, runs every check, converts to PDF with LibreOffice, and writes the files only if all checks pass.

The writer never writes files. It returns one JSON object on stdout; the scripts do all file work.

## Setup

```
python3 -m venv ~/.hermes/venvs/resume-writer
~/.hermes/venvs/resume-writer/bin/pip install python-docx==1.2.0
hermes profile create resume-writer --no-skills --description "Tailored resumes and cover letters"
cp resume-writer/SOUL.example.md ~/.hermes/profiles/resume-writer/SOUL.md   # then edit the placeholders
```

Needs `soffice` (LibreOffice) and `pdfinfo` (poppler) on PATH. The profile's `config.yaml` sets the model; the toolsets were limited with `hermes -p resume-writer tools disable ...` so only `web` stays on. `resume_request.py` also passes `-t web --reasoning high` on every call.

## CLI

```
resume_request.py CODE                        # start a draft for a job card (4 or 6 character code)
resume_request.py CODE --feedback 'text'      # revise the draft; overwrites only this job's files
resume_request.py CODE --answer '1=a;2=b'     # answer the writer's questions (status must be needs_input)
resume_request.py CODE --approve              # mark a ready draft approved (no files change)
resume_request.py CODE --message 'text'       # a chat message: answers questions when needs_input, else a revision
resume_request.py CODE --undo                  # re-render the version before the last revision, as a new version
resume_request.py CODE --status               # print the stored state
```

The board's Messages screen drives all of these. Each successful render saves `<CODE>.spec.v<N>.json` (a draft, a revision or an undo), the chat is kept in `<CODE>.chat.json` (last 200 messages), and `SIGTERM` (the Stop button) restores the previous status and posts a short "Stopped" note. `--approve` still only marks the files approved.

Exit codes: 0 ready / needs_input / approved / status; 1 failed; 2 usage error; 3 another run for this code holds the lock.

Output is one line, for example `ready ABCD: resume <path> | letter <path>`.

```
resume_render.py SPEC.json [--out-dir DIR] [--template-dir DIR] [--replace-resume PDF] [--replace-letter PDF]
```

Prints one JSON line. Exit 0 when files were written, 1 when a check failed (nothing written).

Personal settings (name, email, the roles in your templates, skills never to claim, summary phrases) live in
`~/.hermes/job-agent.json` (`resume` block; see `config/job-agent.example.json`). Environment overrides: `JOBS_STATE_DIR` (default `~/.hermes/state`), `JOBS_EXPORT_DIR` (default `~/Documents/Job Applications`), `JOBS_TEMPLATE_DIR`, `RESUME_PYTHON` (renderer Python, default the venv above), `HERMES_BIN`.

## Spec schema

```json
{
  "job": {"company": "Example Corp", "title": "Full Stack Developer", "template": "software"},
  "keywords": ["React", "Node.js", "PostgreSQL", "Docker", "Agile", "Git", "TypeScript", "SQL"],
  "resume": {
    "summary": "Full Stack Developer with 4+ years of experience ... Example Studio ... contract projects.",
    "skills": ["Frontend: Angular, React, ...", "..."],
    "experience": [
      {"role": "Full Stack Developer", "dates": "Jan 2024 – Present (2 years)", "bullets": ["...", "..."]},
      {"role": "Website Developer", "dates": "Jun 2021 – Dec 2023 (2 years, 7 months)", "bullets": ["..."]},
      {"role": "Web Developer", "dates": "Oct 2020 – May 2021 (8 months)", "bullets": ["..."]}
    ]
  },
  "letter": {"paragraphs": ["body 1", "body 2", "body 3", "body 4"]}
}
```

- `job.template`: `software` (3 roles, each needs `role` and `dates`) or `odd-jobs` (4 roles, `bullets` only; the template's company and date lines stay fixed).
- `job.title`: the exact posting title. The summary must start with it.
- `keywords`: at least 8, all true for you and taken from the posting or the careers page.
- `resume.skills`: software uses `"Group: skill, skill"` strings (label is bolded); odd-jobs uses plain strings.
- `resume.experience`: 2 to 5 bullets per role, in template order.
- `letter.paragraphs`: 3 to 5 body paragraphs. No greeting or sign-off; the template supplies "To Hiring Manager," and "Regards,".

### Checks (renderer)

Errors block the write; nothing is saved on any failure.

- Spec: summary starts with the exact title, contains your `years_phrase` (bold) and, if set, your `employer_phrase`; at least 8 keywords; 2 to 5 bullets per role; exact role count.
- Banned text in the resume or letter: every pattern in `resume.banned_claims` (skills you must never claim), plus work permit, work authorization, availability, notice period, start right away, thank you, Dear Hiring Manager, Sincerely, coursework, certification, Udemy, objective, the pre-rewrite degree wording, "Tailor this section", leftover brackets.
- At least 8 keywords present in the rendered resume text.
- Header lines match the template.
- Letter body 150 to 260 words; opener "To Hiring Manager," and closer "Regards,".
- PDFs: resume and letter exactly 1 page each (`pdfinfo`); under 1 MB; mailto link present; resume has an https link when the template has one.

If `resume.degree_rewrite` is set (for example `["Computing Science", "Computer Science"]`), the renderer rewrites that wording in the template.

### File names

- `Resumes/Resume-<Your-Name>-<Company>-<Role>.pdf` (plus a `.docx` copy)
- `Cover Letters/Cover-Letter-<Your-Name>-<Company>.pdf` (plus a `.docx` copy)

Names are slugged (non-alphanumerics become `-`). A new name never overwrites an existing file: a collision gets `-2`, `-3`. A re-render replaces only the paths passed as `--replace-*`, which `resume_request.py` sets from the job's own state.

## Request flow

1. Lock: `<state>/resume_requests/<CODE>.lock` (pid file). A live pid blocks a second run (exit 3); a stale lock is removed.
2. Prompt: `<CODE>.prompt.txt` holds the rules, profile facts from the files in `resume.profile_files`, the template roles and dates, the posting inside `<posting>` tags (treated as untrusted), and any answers, feedback or failed checks.
3. Writer: `hermes -p resume-writer chat --oneshot -Q --ignore-rules --query-file <prompt> -t web --reasoning high`.
4. If the reply is `{"status": "NEEDS_INPUT", "questions": [...]}`, the state becomes `needs_input`.
5. Otherwise the JSON spec is saved as `<CODE>.spec.json` and rendered. A failed check sends the errors back to the writer (up to 3 attempts in total).
6. On success the state is `ready`; `--approve` marks it `approved`. Nothing is sent anywhere.

## State file

`<JOBS_STATE_DIR or ~/.hermes/state>/resume_requests/<CODE>.json`

```json
{
  "code": "ABCD",
  "company": "Example Corp",
  "title": "Full Stack Developer",
  "link": "https://...",
  "status": "ready",
  "step": "waiting for approval",
  "attempt": 1,
  "questions": [],
  "answers": {},
  "feedback": "",
  "version": 1,
  "versions": [{"n": 1, "kind": "draft", "base": 0}],
  "errors": [],
  "error": "",
  "files": {"resume_pdf": "...", "resume_docx": "...", "letter_pdf": "...", "letter_docx": "..."},
  "pages": {"resume": 1, "letter": 1},
  "keywords_found": ["React", "..."],
  "spec_path": "...",
  "created_at": "2026-...",
  "updated_at": "2026-..."
}
```

Statuses: `queued`, `reading`, `writing_resume`, `checking`, `needs_input`, `ready`, `failed`, `approved`. The writer returns resume and letter together, so `writing_letter` is not used.

## Tests

```
~/.hermes/venvs/resume-writer/bin/python -I tests/test_resume_helpers.py
```

Uses a temp HOME, a temp board DB, a fake `hermes` that returns canned replies, a temp export folder and synthetic Word templates generated by `tests/templates_fixture.py`. It never touches your real templates, `Resumes/` or `Cover Letters/`. Needs LibreOffice (`soffice`) and poppler (`pdfinfo`).

## Templates

Your Word templates go in `<export_dir>/Templates/` (or `JOBS_TEMPLATE_DIR`): `Resume-Template-Software-Developer.docx`,
`Resume-Template-Odd-Jobs.docx` and `Cover-Letter-Template.docx`. The renderer finds content by paragraph **style id**, so
build them with these styles: `AAName`, `AAContact` (hyperlinks must be real links), `AASection`, `AABody`, `AASkill`,
`AAJobTitle`, `AAJobDates`, `AABullet`, `AAReason` (odd-jobs), and for the letter `AALetterBody`, `AALetterHeading`,
`AALetterItem`. `tests/templates_fixture.py` builds a complete working set you can copy and restyle.

## Deploy

`./deploy.sh` installs both scripts with the rest of the project. Create the `resume-writer` profile and `SOUL.md` once
(see Setup).
