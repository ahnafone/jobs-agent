#!/usr/bin/env python3
"""Write the application tracker: one self-contained HTML page + a CSV, from the `jobs` kanban board.

  jobs_export.py            regenerate  <export dir>/jobs-board.html and applications.csv

Default export dir: ~/Documents/Job Applications (override with JOBS_EXPORT_DIR). The board is read-only here.
jobs_add.py / jobs_mark.py / jobs_digest.py call this automatically after they change something.
Card text is untrusted: the HTML builds every cell with textContent and only links http(s) URLs.
"""
import csv
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_common as C  # noqa: E402

COLUMNS = ["code", "stage", "fit", "title", "company", "location", "pay", "posted", "added", "applied_date",
           "last_response", "link", "source"]


def read_rejects(limit=300):
    out = []
    try:
        lines = C.rejects_path().read_text().splitlines()[-limit:]
    except OSError:
        return out
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def rows_for(cards):
    rows = []
    for c in cards:
        lr = c.last_response
        rows.append({
            "code": c.code, "stage": c.stage, "fit": c.fit if c.fit is not None else "", "title": c.title,
            "company": c.company, "location": c.location, "pay": c.pay, "posted": c.posted, "added": c.added,
            "applied_date": c.applied_date,
            "last_response": (f"{lr['date']} {lr['type']}: {lr['text']}" if lr else ""),
            "link": c.link, "source": c.source, "reason": c.reason, "summary": c.summary,
        })
    return rows


def csv_safe(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def build_csv(rows, rejects):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c.replace("_", " ") for c in COLUMNS])
    for r in rows:
        w.writerow([csv_safe(r.get(c, "")) for c in COLUMNS])
    for x in rejects:
        w.writerow(["", "Not eligible", "", csv_safe(x.get("title")), csv_safe(x.get("company")), csv_safe(x.get("location")),
                    "", "", csv_safe(x.get("date")), "", csv_safe(x.get("reason")), csv_safe(x.get("link")), csv_safe(x.get("source"))])
    return buf.getvalue()


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job applications</title>
<style>
 :root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--mute:#6b7280;--line:#e3e6eb;--acc:#2563eb}
 @media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--ink:#e6e9ef;--mute:#98a1b3;--line:#2b3240;--acc:#6ea0ff}}
 *{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--ink)}
 header{padding:16px 20px;border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:2}
 h1{margin:0 0 4px;font-size:18px}.sub{color:var(--mute);font-size:12px}
 .bar{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;align-items:center}
 .chip{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:4px 10px;border-radius:999px;cursor:pointer;font-size:13px}
 .chip.on{background:var(--acc);border-color:var(--acc);color:#fff}
 input[type=search],input[type=number]{padding:6px 9px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}
 input[type=search]{min-width:240px}
 main{padding:14px 20px;overflow-x:auto}
 table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:10px}
 th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
 th{cursor:pointer;white-space:nowrap;font-size:12px;color:var(--mute);user-select:none}
 tr.row:hover{background:rgba(127,127,127,.08)}tr.det td{background:rgba(127,127,127,.06);color:var(--mute)}
 .code{font-family:ui-monospace,monospace;font-size:12px}
 .b{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;color:#fff}
 .New{background:#64748b}.Applied{background:#2563eb}.Assessment{background:#7c3aed}.Interview{background:#d97706}
 .Offer{background:#059669}.Rejected{background:#b91c1c}.NotEligible{background:#9ca3af}
 .fit{font-weight:600}a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
 .empty{padding:24px;color:var(--mute)}
</style></head><body>
<header><h1>Job applications</h1><div class="sub" id="sub"></div>
<div class="bar"><span id="chips"></span><input type="search" id="q" placeholder="Search title, company, location">
<label class="sub">min fit <input type="number" id="minfit" min="0" max="100" value="0" style="width:64px"></label></div></header>
<main><table><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table><div class="empty" id="empty" hidden>Nothing matches.</div></main>
<script>
const DATA = __DATA__;
const COLS = [["code","Code"],["stage","Status"],["fit","Fit"],["title","Title"],["company","Company"],["location","Location"],
 ["pay","Pay"],["posted","Posted"],["added","Added"],["applied_date","Applied"],["last_response","Last response"],["link","Link"]];
const STAGES = ["All","New","Applied","Assessment","Interview","Offer","Rejected","Not eligible"];
let stage = "All", sortKey = "added", sortDir = -1;
const $ = id => document.getElementById(id);
function cell(tr, text){ const td=document.createElement("td"); td.textContent=text==null?"":String(text); tr.appendChild(td); return td; }
function rows(){
  const q=$("q").value.trim().toLowerCase(), mf=+$("minfit").value||0;
  let r = DATA.rows.filter(x => stage==="All" ? x.stage!=="Not eligible" : x.stage===stage);
  if(q) r=r.filter(x => (x.title+" "+x.company+" "+x.location+" "+x.code).toLowerCase().includes(q));
  if(mf) r=r.filter(x => x.fit==="" || +x.fit>=mf);
  r.sort((a,b)=>{let A=a[sortKey],B=b[sortKey]; if(sortKey==="fit"){A=A===""?-1:+A;B=B===""?-1:+B}
    else{A=String(A||"").replace("~","").toLowerCase();B=String(B||"").replace("~","").toLowerCase();
         if(A==="unknown")A="";if(B==="unknown")B=""}
    return (A<B?-1:A>B?1:0)*sortDir});
  return r;
}
function render(){
  const body=$("body"); body.textContent="";
  const r=rows(); $("empty").hidden=r.length>0;
  for(const x of r){
    const tr=document.createElement("tr"); tr.className="row";
    cell(tr,x.code).className="code";
    const s=cell(tr,""); const b=document.createElement("span"); b.className="b "+x.stage.replace(" ",""); b.textContent=x.stage; s.appendChild(b);
    cell(tr,x.fit).className="fit"; cell(tr,x.title); cell(tr,x.company); cell(tr,x.location); cell(tr,x.pay);
    cell(tr,x.posted); cell(tr,x.added); cell(tr,x.applied_date); cell(tr,x.last_response||x.reason_short||"");
    const l=cell(tr,""); if(/^https?:\/\//i.test(x.link||"")){const a=document.createElement("a");a.href=x.link;a.target="_blank";a.rel="noopener noreferrer";a.textContent="open";l.appendChild(a)}
    tr.addEventListener("click",e=>{ if(e.target.tagName==="A")return; const nx=tr.nextSibling;
      if(nx&&nx.className==="det"){nx.remove();return}
      const d=document.createElement("tr"); d.className="det"; const td=document.createElement("td"); td.colSpan=COLS.length;
      td.textContent=[x.reason&&("Why: "+x.reason), x.summary&&("Summary: "+x.summary), x.source&&("Source: "+x.source)].filter(Boolean).join("\n");
      td.style.whiteSpace="pre-wrap"; d.appendChild(td); tr.after(d); });
    body.appendChild(tr);
  }
}
function head(){
  const h=$("head"); h.textContent="";
  for(const [k,label] of COLS){ const th=document.createElement("th"); th.textContent=label+(k===sortKey?(sortDir>0?" ▲":" ▼"):"");
    th.addEventListener("click",()=>{ if(sortKey===k)sortDir*=-1; else{sortKey=k;sortDir=(k==="fit"||k==="added"||k==="applied_date")?-1:1} head();render()}); h.appendChild(th); }
}
function chips(){
  const c=$("chips"); c.textContent="";
  for(const s of STAGES){ const n=DATA.rows.filter(x=>s==="All"?x.stage!=="Not eligible":x.stage===s).length;
    const b=document.createElement("button"); b.className="chip"+(s===stage?" on":""); b.textContent=s+" "+n;
    b.addEventListener("click",()=>{stage=s;chips();render()}); c.appendChild(b); }
}
$("sub").textContent="Updated "+DATA.updated+" · "+DATA.rows.filter(x=>x.stage!=="Not eligible").length+" tracked · click a row for details · open Discord #jobs to add links";
$("q").addEventListener("input",render); $("minfit").addEventListener("input",render);
chips(); head(); render();
</script></body></html>
"""


def build_html(rows, rejects):
    rr = list(rows)
    for x in rejects:
        rr.append({"code": "", "stage": "Not eligible", "fit": "", "title": x.get("title", ""), "company": x.get("company", ""),
                   "location": x.get("location", ""), "pay": "", "posted": "", "added": x.get("date", ""),
                   "applied_date": "", "last_response": "", "reason_short": x.get("reason", ""), "link": x.get("link", ""),
                   "source": x.get("source", ""), "reason": x.get("reason", ""), "summary": ""})
    data = {"rows": rr, "updated": datetime.now(C.tz()).strftime("%Y-%m-%d %H:%M")}
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026") \
        .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return HTML.replace("__DATA__", blob)


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def export():
    cards = C.load_cards()
    rows = rows_for(cards)
    rej = read_rejects()
    d = C.export_dir()
    write_atomic(d / "jobs-board.html", build_html(rows, rej))
    write_atomic(d / "applications.csv", build_csv(rows, rej))
    return d / "jobs-board.html", len(rows), len(rej)


def safe_export():
    """For callers that must not fail because of the export."""
    try:
        return export()
    except Exception as ex:  # noqa: BLE001
        print(f"(board export skipped: {ex})", file=sys.stderr)
        return None


if __name__ == "__main__":
    try:
        p, n, r = export()
        print(f"wrote {p} ({n} cards, {r} not-eligible) and applications.csv")
    except Exception as ex:
        print(f"jobs_export error: {ex}", file=sys.stderr)
        sys.exit(1)
