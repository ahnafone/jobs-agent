#!/usr/bin/env python3
"""Build the static demo into docs/ (what GitHub Pages serves).

  python3 demo/build.py [OUTDIR]        default OUTDIR: docs/

It copies board/static/ and then:
  * injects demo/fixtures.json and demo/mock-api.js before app.js, so every /api/* call is answered in the browser;
  * makes the root-absolute links relative (Pages serves the site under /<repo>/);
  * drops the service worker and install files (no offline cache for a demo);
  * adds a "Demo, sample data" strip.
The real board code is not modified: only the copy in OUTDIR changes.
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "board" / "static"
DEMO = ROOT / "demo"

BANNER_CSS = """<style>
.demo-strip{position:sticky;top:0;z-index:9999;background:#1f2a44;color:#fff;font:500 13px/1.4 system-ui,sans-serif;
  padding:6px 12px;text-align:center}
.demo-strip a{color:#9db7ff}
</style>"""
def banner():
    repo = os.environ.get("GITHUB_REPOSITORY", "")      # set by GitHub Actions, e.g. owner/jobs-agent
    link = f' <a href="https://github.com/{repo}">Source on GitHub</a>' if re.fullmatch(r"[\w.-]+/[\w.-]+", repo) else ""
    return ('<div class="demo-strip" role="note">Demo with made-up sample data. Changes stay in your browser and reset on reload.'
            + link + "</div>")


def sub_once(text, old, new, what):
    if text.count(old) != 1:
        raise SystemExit(f"build.py: expected exactly one {what!r} in the board files, found {text.count(old)}. "
                         "The board changed; update demo/build.py.")
    return text.replace(old, new)


def main():
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "docs"
    inside = out == ROOT or ROOT in out.parents
    if out == ROOT or out in ROOT.parents or (inside and out != ROOT / "docs"):
        raise SystemExit(f"build.py: refusing to write to {out}: inside the repo only docs/ is allowed "
                         "(or pick a folder outside it)")
    if out.exists():
        # only replace a folder this script made (it always writes .nojekyll) or an empty one
        if (not out.is_dir() or (any(out.iterdir()) and not (out / ".nojekyll").exists())
                or (out / ".git").exists()):
            raise SystemExit(f"build.py: {out} exists and was not made by this script; remove it first")
        shutil.rmtree(out)
    shutil.copytree(STATIC, out, ignore=shutil.ignore_patterns("sw.js", "manifest.webmanifest"))
    fixtures = json.loads((DEMO / "fixtures.json").read_text(encoding="utf-8"))
    payload = json.dumps(fixtures, ensure_ascii=False).replace("<", "\\u003c")
    mock = (DEMO / "mock-api.js").read_text(encoding="utf-8")

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = sub_once(html, '<link rel="manifest" href="/manifest.webmanifest">\n', "", "manifest link")
    html = sub_once(html, 'href="/icons/icon.svg"', 'href="icons/icon.svg"', "icon link")
    html = sub_once(html, 'href="/icons/apple-touch-icon.png"', 'href="icons/apple-touch-icon.png"', "apple icon link")
    html = sub_once(html, "<body>", "<body>\n" + BANNER_CSS + "\n" + banner(), "<body>")
    html = sub_once(html, '<script src="app.js"></script>',
                    f'<script id="demo-fixtures" type="application/json">{payload}</script>\n<script>\n{mock}\n</script>\n'
                    '<script src="app.js"></script>', "app.js script tag")
    (out / "index.html").write_text(html, encoding="utf-8")

    js = (STATIC / "app.js").read_text(encoding="utf-8")
    js = sub_once(js, 'if ("serviceWorker" in navigator) {', 'if (false) {', "service worker registration")
    js = sub_once(js, '"applications-board.prefs"', '"jobs-agent-demo.prefs"', "prefs storage key")   # github.io origins are shared
    (out / "app.js").write_text(js, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    left = [m for m in re.findall(r'(?:src|href)="(/[^"]*)"', html) if not m.startswith("/api/")]
    if left:
        raise SystemExit(f"build.py: root-absolute links remain in index.html: {left}")
    print(f"demo built in {out} ({sum(1 for _ in out.rglob('*') if _.is_file())} files)")


if __name__ == "__main__":
    main()
