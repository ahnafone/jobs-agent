#!/usr/bin/env python3
"""Render a tailored resume and cover letter from a JSON spec.

Fills the Word templates in `Templates/` with python-docx (styles, spacing and hyperlinks are
kept because only run text and paragraph copies change), converts them to PDF with LibreOffice,
checks the result, and only then writes the files into `Resumes/` and `Cover Letters/`.

Usage:
  resume_render.py SPEC.json [--out-dir DIR] [--template-dir DIR]
                   [--replace-resume PATH] [--replace-letter PATH]

--replace-* names a file this same job already saved (a draft edit). Only that path is overwritten.
Without it, a new name is picked and an existing file is never overwritten.

Prints one JSON line. Exit 0 when the files were written, 1 when a check failed (nothing written).
Needs python-docx: run with the resume-writer venv (~/.hermes/venvs/resume-writer/bin/python).
"""
import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from docx import Document
    from docx.text.paragraph import Paragraph
except ImportError as exc:  # pragma: no cover - reported as a JSON error below
    Document = None
    IMPORT_ERROR = str(exc)
else:
    IMPORT_ERROR = ""

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jobs_common import export_dir  # noqa: E402  (read-only use of the shared path helper)
import jobs_config as CFG  # noqa: E402

TEMPLATE_FILES = {
    "software": "Resume-Template-Software-Developer.docx",
    "odd-jobs": "Resume-Template-Odd-Jobs.docx",
    "letter": "Cover-Letter-Template.docx",
}
LETTER_OPENER = "To Hiring Manager,"
LETTER_CLOSER = "Regards,"
MIN_KEYWORDS = 8
MAX_PDF_BYTES = 1_000_000

# (pattern, label). Applied to the rendered resume and letter text, case-insensitive.
# Style rules that hold for anyone. Skills the person must never claim come from the config (resume.banned_claims).
BANNED_STYLE = [
    (r"work\s+permit", "work permit"),
    (r"work\s+authori[sz]ation", "work authorization"),
    (r"start\s+right\s+away", "start right away"),
    (r"\b(?:my|his|your)\s+availability\b|\bavailable\s+(?:to\s+start|immediately|on\s+short\s+notice)\b|\bimmediately\s+available\b", "availability"),
    (r"notice\s+period", "notice period"),
    (r"\bthank[\s-]you\b", "thank you"),
    (r"\bdear\s+hiring\b", "Dear Hiring Manager"),
    (r"\bsincerely\b", "Sincerely"),
    (r"coursework", "coursework"),
    (r"\bcertifications?\b|\bcertified\b", "certification"),
    (r"udemy", "Udemy"),
    (r"(?<![-\w])objective(?![-\w])", "objective"),
    (r"tailor\s+this\s+section", "template note"),
    (r"\[|\]", "bracket placeholder"),
    (r"\bpassionate\b", "passionate"),
    (r"\bdelve\b", "delve"),
]


def years_phrase():
    return CFG.resume()["years_phrase"]


def employer_phrase():
    return CFG.resume()["employer_phrase"]


def banned():
    """Style rules + the configured never-claim list + the pre-rewrite degree wording."""
    r = CFG.resume()
    out = list(BANNED_STYLE)
    out += [(pat, label) for pat, label in (tuple(x) for x in r["banned_claims"])]
    old, new = r["degree_rewrite"]
    if old and old not in new:      # skip when the fix still contains the old wording (it would ban itself)
        out.append((re.escape(old).replace(r"\ ", r"\s+"), old))
    return out


# ---------- spec validation ----------
def _strs(value):
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def validate_spec(spec, n_roles):
    """Structure and content rules that do not need the rendered document."""
    errors = []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"]
    for k in ("job", "resume", "letter"):
        if not isinstance(spec.get(k), dict):
            return [f"{k} must be a JSON object"]
    job, resume, letter = spec["job"], spec["resume"], spec["letter"]
    title = job.get("title") if isinstance(job, dict) else None
    company = job.get("company") if isinstance(job, dict) else None
    if not (isinstance(title, str) and title.strip()):
        errors.append("job.title (exact posting title) is required")
    if not (isinstance(company, str) and company.strip()):
        errors.append("job.company is required")
    if job.get("template") not in ("software", "odd-jobs"):
        errors.append("job.template must be 'software' or 'odd-jobs'")

    keywords = spec.get("keywords")
    if not _strs(keywords) or len(keywords) < MIN_KEYWORDS:
        errors.append(f"keywords: need at least {MIN_KEYWORDS} job keywords (strings from the posting)")

    summary = resume.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("resume.summary is required")
    else:
        if title and not summary.startswith(title):
            errors.append("resume.summary must start with the exact posting title (job.title)")
        if years_phrase() not in summary:
            errors.append(f"resume.summary must contain '{years_phrase()}'")
        if employer_phrase() and employer_phrase() not in summary:
            errors.append(f"resume.summary must mention {employer_phrase()}")

    if not _strs(resume.get("skills")):
        errors.append("resume.skills must be a non-empty list of strings")

    exp = resume.get("experience")
    if not isinstance(exp, list) or len(exp) != n_roles:
        errors.append(f"resume.experience must have exactly {n_roles} roles (template order)")
    else:
        for i, role in enumerate(exp, 1):
            if not isinstance(role, dict):
                errors.append(f"experience {i}: must be an object")
                continue
            if job.get("template") == "software":
                if not (isinstance(role.get("role"), str) and role["role"].strip()):
                    errors.append(f"experience {i}: 'role' (posting title for this role) is required")
                if not (isinstance(role.get("dates"), str) and role["dates"].strip()):
                    errors.append(f"experience {i}: 'dates' (e.g. 'Jan 2019 – Present (7 years, 11 months)') is required")
            bullets = role.get("bullets")
            if not _strs(bullets):
                errors.append(f"experience {i}: bullets must be a non-empty list of strings")
            elif not 2 <= len(bullets) <= 5:
                errors.append(f"experience {i}: use 2 to 5 bullets (has {len(bullets)})")

    paras = letter.get("paragraphs")
    if not _strs(paras) or not 3 <= len(paras) <= 5:
        errors.append("letter.paragraphs must be 3 to 5 body paragraphs (strings)")
    return errors


# ---------- docx helpers ----------
def _sections(doc):
    """Find the paragraphs to fill, by paragraph style id and section heading."""
    info = {"summary": None, "skills": [], "skills_para": None, "roles": [], "edu": None}
    section = None
    for p in doc.paragraphs:
        sid = p.style.style_id
        if sid == "AASection":
            section = p.text.strip().lower()
            continue
        if section == "summary" and sid == "AABody" and info["summary"] is None:
            info["summary"] = p
        elif section == "skills":
            if sid == "AASkill":
                info["skills"].append(p)
            elif sid == "AABody" and info["skills_para"] is None:
                info["skills_para"] = p
        elif section in ("experience", "work experience"):
            if sid == "AAJobTitle":
                info["roles"].append({"title": p, "dates": None, "bullets": []})
            elif sid == "AAJobDates" and info["roles"]:
                info["roles"][-1]["dates"] = p
            elif sid == "AABullet" and info["roles"]:
                info["roles"][-1]["bullets"].append(p)
        elif section == "education" and sid == "AABody" and info["edu"] is None:
            info["edu"] = p
    return info


def _set_segments(para, segments):
    """Replace a paragraph's runs with (text, bold) segments, keeping the paragraph's style."""
    for r in list(para._p.xpath("./w:r")):
        para._p.remove(r)
    for text, bold in segments:
        if text:
            run = para.add_run(text)
            run.bold = bool(bold)


def _replace_block(old, texts, segments_for):
    """Replace a run of same-style paragraphs with new ones copied from the first old paragraph."""
    anchor = old[0]._p
    parent = old[0]._parent
    proto = copy.deepcopy(anchor)
    for text in texts:
        el = copy.deepcopy(proto)
        anchor.addprevious(el)
        _set_segments(Paragraph(el, parent), segments_for(text))
    for p in old:
        p._p.getparent().remove(p._p)


def _fill_summary(info, title, summary):
    p = info["summary"]
    years = years_phrase()
    i = summary.index(years)
    head, tail = summary[:len(title)], summary[len(title):i]
    rest = summary[i + len(years):]
    _set_segments(p, [(head, True), (tail, False), (years, True), (rest, False)])


def _fill_software_skills(info, lines):
    old = info["skills"]

    def seg(line):
        label, sep, rest = line.partition(":")
        if sep:
            return [(label + ": ", True), (rest.lstrip(), False)]
        return [(line, False)]

    _replace_block(old, lines, seg)


def _fill_roles(info, roles_spec, software):
    for role_info, spec_role in zip(info["roles"], roles_spec):
        if software:
            p = role_info["title"]
            company_part = p.text.split(" | ", 1)[1]
            _set_segments(p, [(f"{spec_role['role'].strip()} | {company_part}", False)])
            _set_segments(role_info["dates"], [(spec_role["dates"].strip(), False)])
        _replace_block(role_info["bullets"], spec_role["bullets"], lambda t: [(t, False)])


def _fill_letter(doc, paragraphs):
    ps = doc.paragraphs
    texts = [p.text.strip() for p in ps]
    i_open = texts.index(LETTER_OPENER)
    i_close = texts.index(LETTER_CLOSER)
    old = ps[i_open + 1:i_close]
    anchor = ps[i_close]._p
    parent = ps[i_close]._parent
    proto = copy.deepcopy(old[0]._p)
    for text in paragraphs:
        el = copy.deepcopy(proto)
        anchor.addprevious(el)
        _set_segments(Paragraph(el, parent), [(text, False)])
    for p in old:
        p._p.getparent().remove(p._p)


def _fix_degree(doc):
    """Optional wording fix (config resume.degree_rewrite = [from, to]), e.g. a degree name the templates spell differently."""
    old, new = CFG.resume()["degree_rewrite"]
    if not old:
        return
    for p in doc.paragraphs:
        for r in p.runs:
            if old in r.text:
                r.text = r.text.replace(old, new)


def fill_resume(doc, spec):
    job, resume = spec["job"], spec["resume"]
    info = _sections(doc)
    _fill_summary(info, job["title"].strip(), resume["summary"].strip())
    software = job["template"] == "software"
    if software:
        _fill_software_skills(info, [s.strip() for s in resume["skills"]])
    else:
        _set_segments(info["skills_para"], [(", ".join(s.strip() for s in resume["skills"]), False)])
    _fill_roles(info, resume["experience"], software)
    _fix_degree(doc)


def doc_text_lines(doc):
    return [p.text for p in doc.paragraphs]


# ---------- checks on the rendered document ----------
def check_banned(label, lines):
    blob = "\n".join(lines)
    return [f"{label}: contains '{name}'" for pat, name in banned() if re.search(pat, blob, re.I)]


def keyword_hits(keywords, text):
    hits = []
    for k in keywords:
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(k.strip()) + r"(?![A-Za-z0-9])", text, re.I):
            hits.append(k)
    return hits


def check_resume_doc(doc, spec, template_header):
    errors = []
    lines = doc_text_lines(doc)
    errors += check_banned("resume", lines)
    text = "\n".join(lines)
    hits = keyword_hits(spec["keywords"], text)
    if len(hits) < MIN_KEYWORDS:
        errors.append(f"resume: only {len(hits)} job keywords found, need {MIN_KEYWORDS}")
    if lines[:2] != template_header:
        errors.append("resume: header lines changed from the template")
    summary = next((p for p in doc.paragraphs if p.text.startswith(spec["job"]["title"].strip())), None)
    if summary is None:
        errors.append("resume: summary does not start with the posting title")
    else:
        bold = [r.text for r in summary.runs if r.bold]
        if spec["job"]["title"].strip() not in bold or years_phrase() not in bold:
            errors.append(f"resume: summary title and '{years_phrase()}' must be bold")
    return errors, hits


def check_letter_doc(doc):
    errors = []
    lines = doc_text_lines(doc)
    errors += check_banned("letter", lines)
    stripped = [ln.strip() for ln in lines if ln.strip()]
    if LETTER_OPENER not in stripped or LETTER_CLOSER not in stripped:
        return errors + ["letter: missing 'To Hiring Manager,' or 'Regards,'"]
    i_open, i_close = stripped.index(LETTER_OPENER), stripped.index(LETTER_CLOSER)
    if i_close <= i_open:
        errors.append("letter: 'Regards,' must come after the body")
    body = " ".join(stripped[i_open + 1:i_close])
    words = len(body.split())
    if not 150 <= words <= 260:
        errors.append(f"letter: body is {words} words (aim for about 150 to 220)")
    return errors


def pdf_pages(pdf):
    try:
        out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=60).stdout
        m = re.search(r"^Pages:\s+(\d+)", out, re.M)
        if m:
            return int(m.group(1))
    except (OSError, subprocess.SubprocessError):
        pass
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))


def check_pdf(label, pdf, need_https):
    errors = []
    size = pdf.stat().st_size
    if size >= MAX_PDF_BYTES:
        errors.append(f"{label}: PDF is {size} bytes (limit 1 MB)")
    pages = pdf_pages(pdf)
    if pages != 1:
        errors.append(f"{label}: PDF has {pages} pages (must be exactly 1)")
    uris = b" ".join(re.findall(rb"/URI\s*\(([^)]*)\)", pdf.read_bytes()))
    if f"mailto:{CFG.get('email')}".encode() not in uris:
        errors.append(f"{label}: no mailto hyperlink in the PDF")
    if need_https and not re.search(rb"https?://", uris):
        errors.append(f"{label}: no URL hyperlink in the PDF")
    return errors, pages, size


# ---------- file names ----------
def slug(value, limit=80):
    s = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return s[:limit].rstrip("-") or "Job"


def resume_prefix():
    return f"Resume-{CFG.name_slug()}-"


def letter_prefix():
    return f"Cover-Letter-{CFG.name_slug()}-"


def resume_name(company, title):
    return f"{resume_prefix()}{slug(company)}-{slug(title)}.pdf"


def letter_name(company):
    return f"{letter_prefix()}{slug(company)}.pdf"


def free_path(folder, name):
    candidate = folder / name
    stem, suffix = candidate.stem, candidate.suffix
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def _check_replace(path, folder, prefix):
    if path is None:
        return None
    p = Path(path)
    if p.parent.resolve() != folder.resolve() or not p.name.startswith(prefix) or p.suffix != ".pdf":
        raise ValueError(f"--replace path must be an existing {prefix}* PDF in {folder}")
    return p


def pick_target(folder, name, replace):
    if replace is not None:
        return replace
    return free_path(folder, name)


# ---------- conversion ----------
def to_pdf(paths, workdir):
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) not found")
    profile = tempfile.mkdtemp(prefix="lo-profile-", dir=workdir)
    cmd = [soffice, f"-env:UserInstallation=file://{profile}", "--headless",
           "--convert-to", "pdf", "--outdir", str(workdir), *[str(p) for p in paths]]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if proc.returncode != 0:
        raise RuntimeError(f"soffice failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    pdfs = [Path(workdir) / (p.stem + ".pdf") for p in paths]
    missing = [str(p) for p in pdfs if not p.exists()]
    if missing:
        raise RuntimeError(f"soffice did not write {missing}")
    return pdfs


# ---------- main render ----------
def render(spec, out_dir, template_dir, replace_resume=None, replace_letter=None):
    if Document is None:
        return {"ok": False, "errors": [f"python-docx not available: {IMPORT_ERROR}"]}
    tdir = Path(template_dir)
    tpl = {k: tdir / v for k, v in TEMPLATE_FILES.items()}
    for k, path in tpl.items():
        if not path.exists():
            return {"ok": False, "errors": [f"missing template: {path}"]}

    job_block = spec.get("job") if isinstance(spec, dict) else None
    tkey = job_block.get("template") if isinstance(job_block, dict) else None
    if tkey not in ("software", "odd-jobs"):
        return {"ok": False, "errors": validate_spec(spec, 0)}
    resume_out = Document(str(tpl[tkey]))
    errors = validate_spec(spec, len(_sections(resume_out)["roles"]))
    if errors:
        return {"ok": False, "errors": errors}

    job = spec["job"]
    template_header = [p.text for p in resume_out.paragraphs[:2]]
    try:
        fill_resume(resume_out, spec)
        letter_doc = Document(str(tpl["letter"]))
        _fill_letter(letter_doc, [p.strip() for p in spec["letter"]["paragraphs"]])
    except (ValueError, IndexError, KeyError, TypeError, AttributeError) as exc:
        return {"ok": False, "errors": [f"the draft did not fit the template: {type(exc).__name__}: {exc}"]}

    errors = []
    resume_errors, hits = check_resume_doc(resume_out, spec, template_header)
    errors += resume_errors
    errors += check_letter_doc(letter_doc)
    if errors:
        return {"ok": False, "errors": errors}

    out_dir = Path(out_dir)
    res_dir, let_dir = out_dir / "Resumes", out_dir / "Cover Letters"
    try:
        r_replace = _check_replace(replace_resume, res_dir, resume_prefix())
        l_replace = _check_replace(replace_letter, let_dir, letter_prefix())
    except ValueError as exc:
        return {"ok": False, "errors": [str(exc)]}

    res_pdf_name = resume_name(job["company"], job["title"])
    let_pdf_name = letter_name(job["company"])
    workdir = Path(tempfile.mkdtemp(prefix="resume-render-"))
    try:
        r_docx = workdir / "resume.docx"
        l_docx = workdir / "letter.docx"
        resume_out.save(str(r_docx))
        letter_doc.save(str(l_docx))
        r_pdf, l_pdf = to_pdf([r_docx, l_docx], workdir)
        pdf_errors = []
        # Only the software template carries https links (LinkedIn, GitHub, website); odd-jobs has mailto only.
        e1, res_pages, res_size = check_pdf("resume", r_pdf, need_https=(job["template"] == "software"))
        e2, let_pages, _ = check_pdf("letter", l_pdf, need_https=False)
        pdf_errors += e1 + e2
        if pdf_errors:
            return {"ok": False, "errors": pdf_errors}

        res_out_pdf = pick_target(res_dir, res_pdf_name, r_replace)
        let_out_pdf = pick_target(let_dir, let_pdf_name, l_replace)
        res_out_docx = res_out_pdf.with_suffix(".docx")
        let_out_docx = let_out_pdf.with_suffix(".docx")
        for src, dst in [(r_pdf, res_out_pdf), (r_docx, res_out_docx), (l_pdf, let_out_pdf), (l_docx, let_out_docx)]:
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)          # never leaves a half-written file
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        return {"ok": False, "errors": [f"render failed: {exc}"]}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return {
        "ok": True,
        "errors": [],
        "warnings": [] if res_size < MAX_PDF_BYTES else ["resume PDF is large"],
        "resume_pdf": str(res_out_pdf),
        "resume_docx": str(res_out_docx),
        "letter_pdf": str(let_out_pdf),
        "letter_docx": str(let_out_docx),
        "pages": {"resume": res_pages, "letter": let_pages},
        "keywords_found": hits,
    }


def default_template_dir():
    return Path(os.environ.get("JOBS_TEMPLATE_DIR") or export_dir() / "Templates")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("spec", help="path to the JSON spec")
    ap.add_argument("--out-dir", default=None, help="folder that holds Resumes/ and Cover Letters/")
    ap.add_argument("--template-dir", default=None)
    ap.add_argument("--replace-resume", default=None)
    ap.add_argument("--replace-letter", default=None)
    args = ap.parse_args(argv)
    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [f"cannot read spec: {exc}"]}))
        return 1
    result = render(spec, args.out_dir or export_dir(), args.template_dir or default_template_dir(),
                    args.replace_resume, args.replace_letter)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
