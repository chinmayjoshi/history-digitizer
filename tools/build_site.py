"""Generic static site builder. Turns a book manifest + vision transcripts
into a self-contained static site (GitHub Pages ready), per docs/SPEC.md.

Reads:
    <book>/book.json          manifest
    work/transcripts/*.md     per-page transcripts (YAML frontmatter + markdown)
    work/pages/*.png          scan images (web-optimized into the site)
    stories/*.json            notable stories + comic panels (optional)

Writes:
    site/                     static HTML/CSS/JS + data JSON + optimized images

Usage:
    python3 tools/build_site.py history-of-hindostan [--site-dir site]
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import os
import re
import shutil

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

IMG_MAX_WIDTH = 1400
IMG_QUALITY = 82


# ---------------------------------------------------------------- transcripts

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse the simple `key: value` frontmatter our transcripts use."""
    if not text.startswith("---"):
        return {}, text
    end = text.index("\n---", 3)
    meta: dict[str, str] = {}
    for line in text[3:end].strip().splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1].replace('\\"', '"')
        meta[key.strip()] = val
    return meta, text[end + 4:].strip()


def md_inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*(.+?)\*", r"<em>\1</em>", s)
    return s


def md_to_html(md: str) -> str:
    """Minimal markdown subset: headings, paragraphs, lists, bold/italic."""
    out: list[str] = []
    in_list = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            level = len(m.group(1))
            out.append(f"<h{level}>{md_inline(m.group(2))}</h{level}>")
            continue
        if line.lstrip().startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{md_inline(line.lstrip()[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        out.append(f"<p>{md_inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def split_sections(body: str) -> dict[str, str]:
    """Split transcript body into its ##-named blocks."""
    parts: dict[str, list[str]] = {"Transcription": []}
    current = "Transcription"
    for line in body.splitlines():
        m = re.match(r"^##\s+(Transcription|Plate / Illustration|Annotations)\s*$", line)
        if m:
            current = m.group(1)
            continue
        parts.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in parts.items() if "\n".join(v).strip()}


def load_transcripts(tdir: str) -> list[dict]:
    pages = []
    for path in sorted(glob.glob(os.path.join(tdir, "page_*.md"))):
        with open(path) as f:
            meta, body = parse_frontmatter(f.read())
        blocks = split_sections(body)
        annotations = []
        if "Annotations" in blocks:
            annotations = [ln.lstrip("- ").strip()
                           for ln in blocks["Annotations"].splitlines()
                           if ln.strip().startswith("- ")]
        idx = int(re.search(r"page_(\d+)\.md$", path).group(1))
        pages.append({
            "n": idx,
            "p": meta.get("printed_page") or None,
            "k": meta.get("kind", "text"),
            "s": meta.get("section") or None,
            "plate": meta.get("contains_plate") == "true",
            "platedesc": blocks.get("Plate / Illustration") or None,
            "notes": meta.get("notes") or None,
            "t": md_to_html(blocks.get("Transcription", "")),
            "an": annotations,
        })
    return pages


# ------------------------------------------------------------------ stories

def load_stories(sdir: str) -> list[dict]:
    stories = []
    for path in sorted(glob.glob(os.path.join(sdir, "*.json"))):
        with open(path) as f:
            st = json.load(f)
        st["slug"] = st.get("slug") or os.path.splitext(os.path.basename(path))[0]
        st["summary_html"] = md_to_html(st.get("summary", ""))
        stories.append(st)
    return stories


# ------------------------------------------------------------------- images

def export_images(pages: list[dict], pages_dir: str, site_dir: str) -> None:
    out = os.path.join(site_dir, "assets", "pages")
    os.makedirs(out, exist_ok=True)
    for pg in pages:
        dst = os.path.join(out, f"page_{pg['n']:04d}.jpg")
        if os.path.exists(dst):
            continue
        src = None
        for ext in ("png", "jpg"):
            cand = os.path.join(pages_dir, f"page_{pg['n']:04d}.{ext}")
            if os.path.exists(cand):
                src = cand
                break
        if not src:
            continue
        if Image is None:
            shutil.copyfile(src, dst)
            continue
        img = Image.open(src).convert("RGB")
        if img.width > IMG_MAX_WIDTH:
            h = round(img.height * IMG_MAX_WIDTH / img.width)
            img = img.resize((IMG_MAX_WIDTH, h), Image.LANCZOS)
        img.save(dst, "JPEG", quality=IMG_QUALITY)


# --------------------------------------------------------------------- html

STYLE = """
:root{--parch:#f5eeda;--parch2:#efe5c8;--ink:#2e2418;--accent:#8a5a2b;--accent2:#5b7a52;--line:#d8c9a3}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--parch);color:var(--ink);font-family:Georgia,'Iowan Old Style',serif;line-height:1.65}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1100px;margin:0 auto;padding:0 20px}
header.site{background:#2e2418;color:var(--parch);padding:28px 0}
header.site h1{font-size:1.5rem;font-weight:normal;letter-spacing:.02em}
header.site .sub{opacity:.75;font-style:italic;font-size:.95rem;margin-top:4px}
nav.site{background:#241c12;padding:8px 0}
nav.site a{color:var(--parch);margin-right:22px;font-size:.92rem;text-transform:uppercase;letter-spacing:.08em}
nav.site a.active{border-bottom:2px solid var(--accent);padding-bottom:2px}
main{padding:34px 0 70px}
.card{background:#fbf7ea;border:1px solid var(--line);border-radius:6px;padding:22px 26px;margin-bottom:18px;box-shadow:0 1px 3px rgba(46,36,24,.08)}
h1,h2,h3,h4{font-weight:normal}
.meta-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px 26px;font-size:.95rem}
.meta-grid dt{color:var(--accent);font-size:.78rem;text-transform:uppercase;letter-spacing:.07em}
.cover{max-width:300px;border:1px solid var(--line);border-radius:4px;box-shadow:0 3px 10px rgba(46,36,24,.2)}
.toc{columns:2;column-gap:40px}
.toc a{display:block;padding:3px 0;font-size:.95rem}
.badge{display:inline-block;background:var(--accent2);color:#fff;border-radius:10px;padding:1px 9px;font-size:.75rem;margin-left:8px}
/* reader */
.reader-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;background:#fbf7ea;border:1px solid var(--line);border-radius:6px;padding:10px 14px;position:sticky;top:0;z-index:5}
.reader-bar button,.reader-bar select{background:var(--accent);color:#fff;border:0;border-radius:4px;padding:7px 14px;font-family:inherit;font-size:.9rem;cursor:pointer}
.reader-bar button:hover{background:#74491f}
.reader-bar input[type=search]{flex:1;min-width:160px;padding:7px 10px;border:1px solid var(--line);border-radius:4px;font-family:inherit;background:#fff}
.reader-bar .pg{font-size:.9rem;color:var(--accent);white-space:nowrap}
.panes{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
@media(max-width:860px){.panes{grid-template-columns:1fr}.toc{columns:1}}
.pane{background:#fbf7ea;border:1px solid var(--line);border-radius:6px;padding:16px}
.pane h3{font-size:.8rem;text-transform:uppercase;letter-spacing:.08em;color:var(--accent);border-bottom:1px solid var(--line);padding-bottom:6px;margin-bottom:12px}
.pane.scan{text-align:center}
.pane.scan img{max-width:100%;border:1px solid var(--line);cursor:zoom-in}
.pane.text{font-size:1.02rem}
.pane.text h2,.pane.text h3{margin:14px 0 8px}
.pane.text p{margin-bottom:10px}
.annots{margin-top:18px;border-top:1px dashed var(--line);padding-top:12px;font-size:.9rem}
.annots li{margin-bottom:6px}
.annots .tag{color:var(--accent2);font-weight:bold}
.pnote{font-size:.85rem;font-style:italic;color:#7a6a4f;margin-top:10px}
.hit{background:#fff3bf}
.search-results{margin-top:14px;font-size:.92rem}
.search-results a{display:block;padding:2px 0}
/* plates */
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}
.gallery figure{background:#fbf7ea;border:1px solid var(--line);border-radius:6px;padding:10px}
.gallery img{width:100%;border:1px solid var(--line)}
.gallery figcaption{font-size:.85rem;margin-top:8px;color:#5a4c36}
/* stories + comic */
.comic{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin:18px 0}
.panel{background:linear-gradient(180deg,#fdf9ec,#f3ead0);border:2px solid #2e2418;border-radius:8px;padding:14px;min-height:150px;position:relative;box-shadow:3px 3px 0 rgba(46,36,24,.25)}
.panel .num{position:absolute;top:-12px;left:-12px;background:#2e2418;color:var(--parch);width:26px;height:26px;border-radius:50%;text-align:center;line-height:26px;font-size:.85rem}
.panel .scene{font-style:italic;color:#6b5a40;font-size:.9rem;margin-bottom:10px}
.bubble{background:#fff;border:1.5px solid #2e2418;border-radius:12px;padding:7px 11px;margin:6px 0;font-size:.92rem;position:relative}
.bubble b{color:var(--accent)}
.panel .caption{margin-top:10px;font-size:.9rem;border-top:1px dashed var(--line);padding-top:8px}
footer.site{border-top:1px solid var(--line);padding:22px 0;font-size:.85rem;color:#7a6a4f}
"""

APP_JS = """
let PAGES=[],IDX=0;
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
async function initReader(){
  const res=await fetch('data/pages.json');PAGES=(await res.json()).pages;
  const q=new URLSearchParams(location.search);IDX=Math.min(Math.max(0,parseInt(q.get('p')||'0')),PAGES.length-1);
  const sel=document.getElementById('jump');
  PAGES.forEach((pg,i)=>{const o=document.createElement('option');o.value=i;
    o.textContent=(pg.p?('p.'+pg.p+' — '):'scan '+pg.n+' — ')+(pg.s||pg.k);sel.appendChild(o)});
  sel.onchange=()=>go(parseInt(sel.value));
  document.getElementById('prev').onclick=()=>go(IDX-1);
  document.getElementById('next').onclick=()=>go(IDX+1);
  document.getElementById('search').oninput=onSearch;
  document.onkeydown=e=>{if(e.target.tagName==='INPUT')return;
    if(e.key==='ArrowLeft')go(IDX-1);if(e.key==='ArrowRight')go(IDX+1)};
  render();
}
function go(i){if(i<0||i>=PAGES.length)return;IDX=i;
  history.replaceState(null,'','?p='+i);render();
  window.scrollTo({top:0,behavior:'smooth'});}
function render(){
  const pg=PAGES[IDX];
  document.getElementById('jump').value=IDX;
  document.getElementById('pglabel').textContent='scan '+(pg.n+1)+'/'+PAGES.length+(pg.p?(' · printed p.'+pg.p):'');
  document.getElementById('prev').disabled=IDX===0;
  document.getElementById('next').disabled=IDX===PAGES.length-1;
  const img=document.getElementById('scanimg');
  img.src='assets/pages/page_'+String(pg.n).padStart(4,'0')+'.jpg';
  img.onclick=()=>window.open(img.src,'_blank');
  let h=pg.t||'<p><em>No text on this page.</em></p>';
  if(pg.platedesc)h+='<div class="annots"><b>Plate:</b> '+esc(pg.platedesc)+'</div>';
  if(pg.an&&pg.an.length){h+='<div class="annots"><h3>Annotations</h3><ul>'+
    pg.an.map(a=>{const m=a.match(/^\\[(.+?)\\]\\s*(.*)$/);
      return m?'<li><span class="tag">['+esc(m[1])+']</span> '+esc(m[2])+'</li>':'<li>'+esc(a)+'</li>'}).join('')+'</ul></div>'}
  if(pg.notes)h+='<p class="pnote">Scan note: '+esc(pg.notes)+'</p>';
  document.getElementById('transcript').innerHTML=h;
}
function stripTags(h){const d=document.createElement('div');d.innerHTML=h;return d.textContent}
function onSearch(e){
  const q=e.target.value.trim().toLowerCase();
  const box=document.getElementById('search-results');
  if(q.length<3){box.innerHTML='';return}
  const hits=[];
  PAGES.forEach((pg,i)=>{const txt=stripTags(pg.t||'').toLowerCase();
    const at=txt.indexOf(q);if(at>=0)hits.push({i,pg,snip:txt.slice(Math.max(0,at-60),at+120)})});
  box.innerHTML='<b>'+hits.length+' page(s) match:</b>'+hits.slice(0,40).map(h=>
    '<a href="?p='+h.i+'" onclick="go('+h.i+');return false">'+
    (h.pg.p?('p.'+h.pg.p):'scan '+h.pg.n)+' — …'+esc(h.snip)+'…</a>').join('');
}
"""


def page_html(title: str, nav_active: str, body: str, extra_js: str = "") -> str:
    nav = [("index.html", "Home"), ("reader.html", "Reader"),
           ("plates.html", "Plates"), ("stories.html", "Stories"),
           ("glossary.html", "Glossary")]
    parts = []
    for h, t in nav:
        cls = ' class="active"' if h.split(".")[0] == nav_active else ""
        parts.append(f'<a href="{h}"{cls}>{t}</a>')
    links = "".join(parts)
    js = f"<script>{APP_JS}{extra_js}</script>" if extra_js else ""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="assets/style.css">
</head><body>
<nav class="site"><div class="wrap">{links}</div></nav>
<main><div class="wrap">{body}</div></main>
<footer class="site"><div class="wrap">Digitized from the 1768 edition · scan: Digital Library of India / Indian Culture · transcripts by vision model, may contain errors — check against the scan</div></footer>
{js}</body></html>"""


def build(book_dir: str, site_dir: str, work_dir: str, stories_dir: str) -> None:
    with open(os.path.join(book_dir, "book.json")) as f:
        book = json.load(f)
    pages = load_transcripts(os.path.join(work_dir, "transcripts"))
    stories = load_stories(stories_dir)
    print(f"{len(pages)} transcripts, {len(stories)} stories")

    os.makedirs(site_dir, exist_ok=True)
    os.makedirs(os.path.join(site_dir, "assets"), exist_ok=True)
    os.makedirs(os.path.join(site_dir, "data"), exist_ok=True)
    with open(os.path.join(site_dir, "assets", "style.css"), "w") as f:
        f.write(STYLE)
    export_images(pages, os.path.join(work_dir, "pages"), site_dir)

    # ---- data for reader
    with open(os.path.join(site_dir, "data", "pages.json"), "w") as f:
        json.dump({"book": book["title"], "pages": pages}, f)

    # ---- landing
    title_pages = [p for p in pages if p["k"] == "title"]
    cover_n = title_pages[0]["n"] if title_pages else pages[0]["n"] if pages else 0
    sections = [(i, p) for i, p in enumerate(pages) if p["s"]]
    meta_rows = "".join(f"<dt>{k}</dt><dd>{html.escape(str(v))}</dd>" for k, v in [
        ("Title", book.get("title")), ("Translator / Author", book.get("author")),
        ("Original work", book.get("original_language")),
        ("Published", f"{book.get('year_copyright')} — {book.get('publisher')}"),
        ("Source scan", book.get("collection")),
        ("Pages digitized", f"{len(pages)} / {book.get('pdf_pages')}")] if v)
    toc_items = []
    for i, p in sections[:200]:
        badge = f' <span class="badge">p.{p["p"]}</span>' if p["p"] else ""
        toc_items.append(f'<a href="reader.html?p={i}">{html.escape(p["s"])}{badge}</a>')
    toc = "".join(toc_items)
    plates_n = sum(1 for p in pages if p["plate"])
    landing = f"""
<div class="card" style="display:flex;gap:26px;flex-wrap:wrap">
  <a href="reader.html"><img class="cover" src="assets/pages/page_{cover_n:04d}.jpg" alt="title page"></a>
  <div style="flex:1;min-width:260px">
    <h1>{html.escape(book['title'])}</h1>
    <p style="font-style:italic;color:#6b5a40;margin:8px 0 16px">{html.escape(book.get('subtitle',''))}</p>
    <dl class="meta-grid">{meta_rows}</dl>
    <p style="margin-top:16px"><a href="reader.html"><b>Start reading →</b></a>
       &nbsp;·&nbsp; <a href="plates.html">{plates_n} plates</a>
       &nbsp;·&nbsp; <a href="stories.html">Notable stories</a></p>
  </div>
</div>
<div class="card"><h2>Contents &amp; section landmarks</h2><div class="toc">{toc or '<p>Transcription in progress…</p>'}</div></div>"""
    with open(os.path.join(site_dir, "index.html"), "w") as f:
        f.write(page_html(book["title"], "index", landing))

    # ---- reader
    reader = """
<div class="reader-bar">
  <button id="prev">← Prev</button><button id="next">Next →</button>
  <select id="jump"></select><span class="pg" id="pglabel"></span>
  <input type="search" id="search" placeholder="Search the whole book… (3+ chars)">
</div>
<div id="search-results" class="search-results"></div>
<div class="panes">
  <div class="pane scan"><h3>Original scan (1768)</h3><img id="scanimg" alt="scan"></div>
  <div class="pane text"><h3>Transcription &amp; annotations</h3><div id="transcript"></div></div>
</div>"""
    with open(os.path.join(site_dir, "reader.html"), "w") as f:
        f.write(page_html(f"Reader — {book['title']}", "reader", reader,
                          extra_js="\ninitReader();"))

    # ---- plates
    figs = "".join(
        f'<figure><a href="reader.html?p={i}"><img loading="lazy" src="assets/pages/page_{p["n"]:04d}.jpg"></a>'
        f'<figcaption>{html.escape(p["platedesc"] or "Plate")} (scan {p["n"]})</figcaption></figure>'
        for i, p in enumerate(pages) if p["plate"])
    with open(os.path.join(site_dir, "plates.html"), "w") as f:
        f.write(page_html("Plates", "plates",
                          f'<div class="card"><h1>Preserved plates &amp; illustrations</h1></div>'
                          f'<div class="gallery">{figs or "<p>None found yet.</p>"}</div>'))

    # ---- stories
    cards = "".join(
        f'<div class="card"><h2><a href="story-{s["slug"]}.html">{html.escape(s["title"])}</a></h2>'
        f'<p style="color:#6b5a40;font-size:.9rem">Source: {s.get("pages","")} · {len(s.get("panels",[]))} comic panels</p>'
        f'{s["summary_html"]}</div>' for s in stories)
    empty_stories = '<div class="card"><p>Coming after transcription completes.</p></div>'
    with open(os.path.join(site_dir, "stories.html"), "w") as f:
        f.write(page_html("Notable stories", "stories",
                          f'<div class="card"><h1>Notable stories from the book</h1>'
                          f'<p>The standout episodes, summarized and retold as lightweight vector comics.</p></div>'
                          + (cards or empty_stories)))
    for s in stories:
        panels = "".join(
            f'<div class="panel"><span class="num">{i+1}</span>'
            f'<div class="scene">{html.escape(pn.get("scene",""))}</div>'
            + "".join(f'<div class="bubble"><b>{html.escape(d.get("speaker",""))}:</b> {html.escape(d.get("text",""))}</div>'
                      for d in pn.get("dialogue", []))
            + f'<div class="caption">{html.escape(pn.get("caption",""))}</div></div>'
            for i, pn in enumerate(s.get("panels", [])))
        body = f"""
<div class="card"><h1>{html.escape(s['title'])}</h1>
<p style="color:#6b5a40">Source pages: {s.get('pages','')} · Figures: {html.escape(', '.join(s.get('figures',[])))}</p>
{s['summary_html']}
<p><b>Why it stands out:</b> {html.escape(s.get('why',''))}</p></div>
<div class="card"><h2>The story as a comic</h2><div class="comic">{panels}</div></div>
<p><a href="stories.html">← All stories</a></p>"""
        with open(os.path.join(site_dir, f"story-{s['slug']}.html"), "w") as f:
            f.write(page_html(s["title"], "stories", body))

    # ---- glossary (aggregated annotations)
    gloss: dict[str, list[str]] = {}
    for p in pages:
        for a in p["an"]:
            m = re.match(r"^\[(.+?)\]\s*(.*)$", a)
            if m:
                gloss.setdefault(m.group(1), []).append(
                    f'{m.group(2)} <a href="reader.html?p={p["n"]}">(scan {p["n"]})</a>')
    ghtml = "".join(f'<div class="card"><h2>[{html.escape(tag)}]</h2><ul>'
                    + "".join(f"<li>{x}</li>" for x in items[:100]) + "</ul></div>"
                    for tag, items in sorted(gloss.items()))
    with open(os.path.join(site_dir, "glossary.html"), "w") as f:
        f.write(page_html("Glossary & annotations", "glossary",
                          f'<div class="card"><h1>Glossary &amp; editorial annotations</h1>'
                          f'<p>Aggregated from the margins of every page, grouped by tag.</p></div>{ghtml}'))
    print(f"site written to {site_dir}/")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("book_dir")
    ap.add_argument("--site-dir", default=None)
    ap.add_argument("--work-dir", default="work")
    ap.add_argument("--stories-dir", default=None)
    args = ap.parse_args()
    site_dir = args.site_dir or os.path.join(args.book_dir, "site")
    stories_dir = args.stories_dir or os.path.join(args.book_dir, "stories")
    build(args.book_dir, site_dir, args.work_dir, stories_dir)


if __name__ == "__main__":
    main()
