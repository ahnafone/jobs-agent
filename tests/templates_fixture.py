"""Build small synthetic Word templates for the resume tests (no real person, no binary files in git).

They mimic the structure resume_render.py expects: paragraph styles by id (AASection, AABody, AASkill,
AAJobTitle, AAJobDates, AABullet, AAReason, AALetterBody ...), a bold title and "5+ years of experience"
in the summary, and mailto / https hyperlinks.

  python3 tests/templates_fixture.py OUTDIR      # writes the three .docx files (needs python-docx)
"""
import sys
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

NAME = "Jane Doe"
EMAIL = "jane.doe@example.com"
STYLES = {  # style id -> (display name, bold, size pt)
    "AAName": ("AA Name", True, 16), "AAContact": ("AA Contact Line", False, 9),
    "AASection": ("AA Section Heading", True, 10.5), "AABody": ("AA Body Text", False, 10),
    "AASkill": ("AA Skill Line", False, 10), "AAJobTitle": ("AA Job Title Line", True, 10),
    "AAJobDates": ("AA Job Dates Line", False, 9.5), "AABullet": ("AA Bullet", False, 10),
    "AAReason": ("AA Reason For Leaving", False, 9.5), "AALetterBody": ("AA Letter Body", False, 10.5),
    "AALetterHeading": ("AA Letter Section Label", True, 10), "AALetterItem": ("AA Letter Item", False, 10),
}


def new_doc():
    d = Document()
    s = d.sections[0]
    s.page_width, s.page_height = Inches(8.5), Inches(11)
    s.left_margin = s.right_margin = Inches(0.75)
    s.top_margin = s.bottom_margin = Inches(0.6)
    for sid, (name, bold, size) in STYLES.items():
        st = d.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        st.style_id = sid
        st.base_style = d.styles["Normal"]
        st.font.name, st.font.size, st.font.bold = "Arial", Pt(size), bold
        st.paragraph_format.space_after = Pt(2)
        st.paragraph_format.space_before = Pt(0)
    return d


def para(d, style, *runs):
    """runs: strings, or (text, bold) tuples, or ('link', text, url)."""
    p = d.add_paragraph(style=STYLES[style][0])
    for r in runs:
        if isinstance(r, tuple) and r[0] == "link":
            link(p, r[1], r[2])
        elif isinstance(r, tuple):
            p.add_run(r[0]).bold = r[1]
        else:
            p.add_run(r)
    return p


def link(p, text, url):
    rid = p.part.relate_to(url, RT.HYPERLINK, is_external=True)
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), rid)
    h.set(qn("w:history"), "1")
    r = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rpr.append(u)
    r.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)
    h.append(r)
    p._p.append(h)


def header(d, full):
    para(d, "AAName", NAME)
    runs = ["Vancouver, BC | (555) 010-0100 | ", ("link", EMAIL, f"mailto:{EMAIL}")]
    if full:
        runs += [" | ", ("link", "linkedin.com/in/janedoe", "https://linkedin.com/in/janedoe"),
                 " | ", ("link", "github.com/janedoe", "https://github.com/janedoe"),
                 " | ", ("link", "example-studio.example", "https://example-studio.example")]
    para(d, "AAContact", *runs)


def software(path):
    d = new_doc()
    header(d, True)
    para(d, "AASection", "Summary")
    para(d, "AABody", ("[Exact job title from the posting]", True), " with ", ("5+ years of experience", True),
         " [in the posting's key terms, only what the profile supports].")
    para(d, "AASection", "Skills")
    for line in ("Frontend: Angular, React, HTML, CSS, TypeScript, JavaScript",
                 "Backend & APIs: Node.js, Express.js, Laravel (PHP), FastAPI",
                 "Databases: MySQL, PostgreSQL, MongoDB, Firestore, SQL query optimization",
                 "Cloud & DevOps: AWS, Google Cloud, Firebase, Docker, Terraform, GitHub Actions, Git",
                 "Automation & Delivery: Agile (Scrum), Jira, Confluence, Figma, workflow automation, code review"):
        label, rest = line.split(": ", 1)
        para(d, "AASkill", (label + ": ", True), rest)
    para(d, "AASkill", "[Tailor this section to the posting: use its exact wording where it is true.]")
    para(d, "AASection", "Experience")
    for co, loc, dates, n in (("Acme Robotics", "Vancouver, BC", "Mar 2023 – Feb 2024 (11 months)", 3),
                              ("Example Studio", "Burnaby, BC", "Jan 2019 – Present ([duration])", 3),
                              ("Globex Corp", "Surrey, BC", "Jun 2021 – Dec 2022 (1 year, 7 months)", 2)):
        para(d, "AAJobTitle", f"[Job title for this posting] | {co} | {loc}")
        para(d, "AAJobDates", dates)
        for _ in range(n):
            para(d, "AABullet", "[Action verb] + [what you did, in the posting's exact words, with a real number].")
    para(d, "AASection", "Education")
    para(d, "AABody", ("BSc Computing Science", True), ", Example University | Sep 2012 – Jun 2016")
    d.save(str(path))


def odd_jobs(path):
    d = new_doc()
    header(d, False)
    para(d, "AASection", "Summary")
    para(d, "AABody", ("[Exact job title from the posting]", True), " [One or two sentences: reliable, hardworking.]")
    para(d, "AASection", "Skills")
    para(d, "AABody", "Customer Service, Retail Operations, Stocking & Inventory, Teamwork")
    para(d, "AASection", "Work Experience")
    jobs = [("Barista (Permanent Part Time), Corner Cafe, Vancouver, BC", 3, "Placeholder reason one."),
            ("Stock Clerk (Permanent Part Time), Example Market, Burnaby, BC", 3, "Placeholder reason two."),
            ("Overnight Associate (Permanent Full Time), Sample Warehouse, Surrey, BC", 2, "Placeholder reason three."),
            ("Crew Member (Permanent Part Time), Example Burgers, Langley, BC", 2, "Placeholder reason four.")]
    for title, n, reason in jobs:
        para(d, "AAJobTitle", title)
        for i in range(n):
            para(d, "AABullet", f"Placeholder duty {i + 1} for this role.")
        para(d, "AAReason", f"Reason for leaving: {reason}")
    para(d, "AASection", "Education")
    para(d, "AABody", ("Bachelor of Science, Computing Science", True), ", Example University | Sep 2012 – Jun 2016")
    d.save(str(path))


def letter(path):
    d = new_doc()
    header(d, False)
    para(d, "AALetterBody", "")
    para(d, "AALetterBody", "To Hiring Manager,")
    for t in ("I am applying for the [exact role title from the posting] role at [company].",
              "[One short paragraph tying your experience to the posting.]",
              "[One paragraph on a specific past project with its numbers.]",
              "[One closing paragraph.]"):
        para(d, "AALetterBody", t)
    para(d, "AALetterBody", "Regards,")
    para(d, "AALetterBody", NAME)
    para(d, "AALetterHeading", "Links")
    para(d, "AALetterItem", ("LinkedIn: ", True), ("link", "linkedin.com/in/janedoe", "https://linkedin.com/in/janedoe"))
    para(d, "AALetterItem", ("GitHub: ", True), ("link", "github.com/janedoe", "https://github.com/janedoe"))
    para(d, "AALetterHeading", "References")
    para(d, "AALetterItem", ("Sam Sample", True), ", Director, Example Studio, ",
         ("link", "sam@example.com", "mailto:sam@example.com"))
    d.save(str(path))


def build(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    software(out / "Resume-Template-Software-Developer.docx")
    odd_jobs(out / "Resume-Template-Odd-Jobs.docx")
    letter(out / "Cover-Letter-Template.docx")
    return out


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "templates-fixture"))
