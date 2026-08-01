"""Generic static site builder. Turns a book manifest + vision transcripts
into a self-contained static site (GitHub Pages ready), per docs/SPEC.md.

Reads:
    <book>/book.json          manifest
    work/transcripts/*.md     per-page transcripts (YAML frontmatter + markdown)
    work/pages/*.png          scan images (web-optimized into the site)
    stories/*.json            notable stories: {slug,title,theme,hook,pages,
                              figures,summary,why,panels:[{scene,dialogue,caption}]}

Writes:
    <site-dir>/               static HTML/CSS/JS + data JSON + optimized images

Usage:
    python3 tools/build_site.py history-of-hindostan --site-dir docs
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

ERAS = [
    {"num": "I", "title": "The Ancients",
     "subtitle": "The History of the Hindoos",
     "range_start": 95, "range_end": 127,
     "span": "Legend — c. 975",
     "blurb": "The fabulous kings of Hindostan: the four ages of the world, the god-king Krishen who tamed elephants, and the dynasties before the first invader."},
    {"num": "II", "title": "The Empire of Ghizni",
     "subtitle": "The Ghaznavids",
     "range_start": 128, "range_end": 257,
     "span": "c. 962 – 1186",
     "blurb": "From the slave Sabuktigin to Mahmud the idol-breaker, the Somnath raid, and Muhammad Ghori's last conquests in India."},
    {"num": "III", "title": "The Empire of Delhi",
     "subtitle": "The Delhi Sultanate",
     "range_start": 258, "range_end": 458,
     "span": "1192 – 1398",
     "blurb": "The slave-sultans and their thrones — Razia, Balban, the Khaljis and the Tughlaqs — until a single capital tears itself apart on the eve of Timur's invasion."},
]

THEME_ICONS = {
    "Origins & Legends": "✦",
    "Rise of Empires": "♜",
    "Battles & Raids": "⚔",
    "Rulers of Uncommon Fate": "♛",
    "The Decline": "⌛",
}

COLOR_THEMES = {
    "Origins & Legends": "origins",
    "Rise of Empires": "rise",
    "Battles & Raids": "battles",
    "Rulers of Uncommon Fate": "rulers",
    "The Decline": "decline",
}


def theme_cls(theme: str) -> str:
    return COLOR_THEMES.get(theme, "rise")


# ---------------------------------------------------------------- transcripts

def parse_frontmatter(text: str) -> tuple[dict, str]:
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
            out.append(f"<h{len(m.group(1))}>{md_inline(m.group(2))}</h{len(m.group(1))}>")
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
        trans = blocks.get("Transcription", "")
        pages.append({
            "n": idx,
            "p": meta.get("printed_page") or None,
            "k": meta.get("kind", "text"),
            "s": meta.get("section") or None,
            "plate": meta.get("contains_plate") == "true",
            "platedesc": blocks.get("Plate / Illustration") or None,
            "notes": meta.get("notes") or None,
            "t": md_to_html(trans),
            "an": annotations,
            "words": len(re.findall(r"\S+", trans)),
        })
    return pages


def extract_chapters(pages: list[dict], tdir: str) -> list[dict]:
    """Find the real chapter/section openings (a SECTION heading whose next
    heading is a 'Reign of' title)."""
    chapters = []
    for path in sorted(glob.glob(os.path.join(tdir, "page_*.md"))):
        idx = int(re.search(r"page_(\d+)\.md$", path).group(1))
        lines = open(path).read().splitlines()
        for i, ln in enumerate(lines):
            if "SECTION" not in ln or not ln.lstrip().startswith("#"):
                continue
            for j in range(i + 1, min(i + 7, len(lines))):
                rl = lines[j].lstrip()
                if rl.startswith("#") and re.match(
                        r"^#{3,4}\s*(The Reign|Of the Reign|The History of the Reign)",
                        rl):
                    num = re.search(r"([IVXLCDM]+)[.:]?\s*$", ln.replace("#", "").strip())
                    chapters.append({
                        "n": idx,
                        "section": num.group(1) if num else "",
                        "title": re.sub(r"^#{3,4}\s*", "", rl).strip(),
                    })
                    break
            break  # one chapter opening per page
    return chapters


# ------------------------------------------------------------------ stories

def load_stories(sdir: str) -> list[dict]:
    stories = []
    for path in sorted(glob.glob(os.path.join(sdir, "*.json"))):
        with open(path) as f:
            st = json.load(f)
        st["slug"] = st.get("slug") or os.path.splitext(os.path.basename(path))[0]
        st.setdefault("theme", "Rise of Empires")
        st.setdefault("hook", st.get("summary", "")[:120])
        st["summary_html"] = md_to_html(st.get("summary", ""))
        stories.append(st)
    return stories


def load_simple(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except OSError:
        return None


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

STYLE = r"""
:root{--ink:#201913;--ink2:#2e2418;--parch:#f3ebd6;--parch2:#e9ddc0;--paper:#fbf7ea;
--ox:#7a2e28;--ox2:#a3483f;--gold:#a8833f;--gold2:#c9ad72;--olive:#5b6b3c;--line:#d7c69d;
--muted:#6f6148;}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:
  radial-gradient(1200px 600px at 80% -10%, rgba(168,131,63,.10), transparent 60%),
  var(--parch);
  color:var(--ink);font-family:Georgia,'Iowan Old Style','Times New Roman',serif;line-height:1.72}
a{color:var(--ox);text-decoration:none}a:hover{color:var(--ox2);text-decoration:underline}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px}
/* nav */
nav.site{position:sticky;top:0;z-index:20;background:rgba(32,25,19,.96);backdrop-filter:blur(6px);
  border-bottom:1px solid #000;padding:0}
nav.site .wrap{display:flex;align-items:center;gap:4px;flex-wrap:wrap}
nav.site .brand{font-variant:small-caps;letter-spacing:.14em;color:var(--gold2);margin-right:auto;
  padding:13px 6px;font-weight:bold}
nav.site a{color:#e8dcc0;padding:13px 14px;font-size:.85rem;text-transform:uppercase;letter-spacing:.09em}
nav.site a:hover{background:rgba(255,255,255,.06);text-decoration:none;color:#fff}
nav.site a.active{color:#fff;border-bottom:2px solid var(--gold);box-shadow:inset 0 -2px 0 var(--gold)}
main{padding:0 0 80px}
/* hero */
.hero{background:linear-gradient(160deg,#241c12 0%,#2e2418 55%,#3a2d1c 100%);color:var(--parch);
  padding:56px 0 60px;border-bottom:5px solid var(--gold)}
.hero .wrap{display:grid;grid-template-columns:300px 1fr;gap:44px;align-items:center}
@media(max-width:760px){.hero .wrap{grid-template-columns:1fr}}.hero .portrait-frame{position:relative}
.hero .portrait-frame::before,.hero .portrait-frame::after{content:"";position:absolute;inset:-16px -14px;
  border:1px solid rgba(201,173,114,.5);transform:rotate(-1.2deg);pointer-events:none}
.hero .portrait-frame::after{inset:-11px -10px;border-color:rgba(201,173,114,.25);
  transform:rotate(.8deg)}
.hero img.portrait{width:100%;display:block;border:1px solid #000;box-shadow:0 12px 34px rgba(0,0,0,.5)}
.hero .kicker{letter-spacing:.24em;text-transform:uppercase;font-size:.72rem;color:var(--gold2);
  margin-bottom:14px}
.hero h1{font-size:clamp(1.7rem,4vw,2.7rem);font-weight:normal;line-height:1.18;margin-bottom:12px}
.hero .subtitle{font-style:italic;color:#d8c8a6;max-width:56ch;margin-bottom:20px}
.hero .byline{font-size:.9rem;color:#cbb992;margin-bottom:26px}
.btn{display:inline-block;background:var(--ox);color:#fff;padding:12px 22px;border-radius:3px;
  font-size:.9rem;letter-spacing:.05em;margin-right:10px;margin-bottom:8px;box-shadow:0 3px 0 #4e1c18}
.btn:hover{background:var(--ox2);color:#fff;text-decoration:none;transform:translateY(-1px)}
.btn.ghost{background:transparent;border:1px solid var(--gold2);color:var(--gold2);box-shadow:none}
.btn.ghost:hover{background:rgba(201,173,114,.12)}
/* stats */
.stats{background:var(--ink2);color:var(--parch);border-bottom:1px solid #000}
.stats .wrap{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;text-align:center}
.stat{padding:20px 8px}
.stat b{display:block;font-size:1.5rem;color:var(--gold2);font-weight:normal;font-variant-numeric:tabular-nums}
.stat span{font-size:.7rem;text-transform:uppercase;letter-spacing:.14em;color:#b9a87f}
/* sections */
section.block{padding:54px 0 8px}
.section-head{display:flex;align-items:baseline;gap:16px;margin-bottom:26px;flex-wrap:wrap}
.section-head h2{font-size:1.6rem;font-weight:normal;letter-spacing:.01em}
.section-head .orn{color:var(--gold);font-size:1.2rem}
.section-head p{margin-left:auto;font-size:.85rem;color:var(--muted);font-style:italic;max-width:40ch}
.lead{max-width:68ch;font-size:1.06rem;margin-bottom:8px}
.lead .cliff{margin-top:14px;padding:16px 20px;border-left:3px solid var(--gold);
  background:var(--paper);border-radius:0 6px 6px 0;font-style:italic;color:var(--ink2)}
/* eras */
.eras{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;margin-top:8px}
@media(max-width:900px){.eras{grid-template-columns:1fr}}
.era{background:var(--paper);border:1px solid var(--line);border-top:4px solid var(--ox);border-radius:6px;
  overflow:hidden;display:flex;flex-direction:column;box-shadow:0 6px 18px rgba(32,25,19,.10);
  transition:transform .18s ease}
.era:hover{transform:translateY(-4px)}
.era .top{position:relative}
.era .top img{width:100%;display:block;aspect-ratio:3/2;object-fit:cover;filter:saturate(.96)}
.era .num{position:absolute;top:-20px;left:16px;background:var(--ox);color:#fff;width:46px;height:46px;
  border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1.2rem;
  border:3px solid var(--paper);box-shadow:0 3px 8px rgba(0,0,0,.3)}
.era .body{padding:22px 20px 20px;display:flex;flex-direction:column;gap:8px;flex:1}
.era h3{font-size:1.2rem;font-weight:normal}
.era .span{color:var(--ox);font-size:.78rem;letter-spacing:.08em;text-transform:uppercase}
.era p{font-size:.92rem;color:var(--muted);flex:1}
.era a.chapters{color:var(--ox);font-size:.85rem;font-variant:small-caps;letter-spacing:.06em}
/* cards generic */
.card{background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:22px 26px;
  margin-bottom:18px;box-shadow:0 1px 3px rgba(46,36,24,.08)}
/* stories */
.chapter-list{columns:2;column-gap:44px}
@media(max-width:760px){.chapter-list{columns:1}}
.chapter-list details{margin-bottom:6px;break-inside:avoid}
.chapter-list summary{cursor:pointer;font-variant:small-caps;letter-spacing:.05em;color:var(--ink2);
  padding:4px 0;border-bottom:1px dotted var(--line)}
.chapter-list summary:hover{color:var(--ox)}
.chapter-list a{display:block;padding:2px 0 2px 14px;font-size:.93rem;color:var(--ink)}
.chapter-list a:hover{color:var(--ox)}
.chapter-list .pg{color:var(--gold);font-size:.78rem;margin-left:6px}
/* story cards */
.story-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:18px}
.story{background:var(--paper);border:1px solid var(--line);border-top:4px solid var(--gold);border-radius:6px;
  padding:18px 20px;display:flex;flex-direction:column;gap:8px;box-shadow:0 5px 14px rgba(32,25,19,.08);
  transition:transform .16s ease}
.story:hover{transform:translateY(-3px)}
.story .tag{font-size:.7rem;letter-spacing:.14em;text-transform:uppercase;color:var(--ox)}
.story h3{font-size:1.12rem;font-weight:normal;line-height:1.3}
.story p{font-size:.92rem;color:var(--muted)}
.story .meta{font-size:.78rem;color:var(--gold);border-top:1px dashed var(--line);padding-top:8px;margin-top:auto}
.story.theme-origins{border-top-color:var(--olive)}
.story.theme-decline{border-top-color:var(--ox)}.story.theme-battles{border-top-color:var(--ox2)}
.story.theme-rulers{border-top-color:var(--gold)}.story.theme-rise{border-top-color:#8a6b3a}
/* plates */
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:18px}
.gallery figure{background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:10px}
.gallery img{width:100%;border:1px solid var(--line);border-radius:2px}
.gallery figcaption{font-size:.85rem;margin-top:8px;color:var(--muted);line-height:1.5}
/* timeline component */
.strip{display:flex;gap:0;margin:26px 0}
.strip .seg{flex:1;text-align:center;padding:10px 6px;border-right:2px solid var(--paper)}
.strip .seg a{color:var(--parch);font-size:.8rem;letter-spacing:.06em;display:block}
/* reader */
.reader-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;background:var(--paper);
  border:1px solid var(--line);border-radius:6px;padding:10px 14px;position:sticky;top:56px;z-index:10;
  box-shadow:0 2px 8px rgba(0,0,0,.08)}
.reader-bar button,.reader-bar select{background:var(--ox);color:#fff;border:0;border-radius:3px;
  padding:7px 14px;font-family:inherit;font-size:.9rem;cursor:pointer}
.reader-bar button:disabled{opacity:.4;cursor:not-allowed}
.reader-bar button:hover:not(:disabled){background:var(--ox2)}
.reader-bar select{background:#fff;color:var(--ink);border:1px solid var(--line)}
.reader-bar input[type=search]{flex:1;min-width:160px;padding:7px 10px;border:1px solid var(--line);
  border-radius:3px;font-family:inherit;background:#fff}
.reader-bar .pg{font-size:.88rem;color:var(--ox);white-space:nowrap}
.panes{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}
@media(max-width:860px){.panes{grid-template-columns:1fr}}
.pane{background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:18px}
.pane h3{font-size:.78rem;text-transform:uppercase;letter-spacing:.1em;color:var(--ox);
  border-bottom:1px solid var(--line);padding-bottom:6px;margin-bottom:14px;display:flex;justify-content:space-between}
.pane h3 .era-tag{color:var(--gold);font-weight:normal;text-transform:none;letter-spacing:.02em}
.pane.scan{text-align:center}
.pane.scan img{max-width:100%;border:1px solid var(--line);cursor:zoom-in}
.pane.text{font-size:1.02rem}
.pane.text h2,.pane.text h3{margin:16px 0 8px}.pane.text h2{font-size:1.25rem}.pane.text h3{font-size:1.1rem}
.pane.text p{margin-bottom:10px}
.annots{margin-top:18px;border-top:1px dashed var(--line);padding-top:12px;font-size:.9rem}
.annots ul{padding-left:18px}.annots li{margin-bottom:6px}
.annots .tag{color:var(--olive);font-weight:bold}
.pnote{font-size:.85rem;font-style:italic;color:var(--muted);margin-top:10px}
.search-results{margin-top:14px;font-size:.92rem}
.search-results a{display:block;padding:3px 0}
.search-results .hit-n{color:var(--gold);font-size:.8rem}
/* story page + comic */
.comic{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:20px;margin:20px 0}
.panel{background:linear-gradient(180deg,#fdf9ec,#f2e8cf);border:2px solid var(--ink);border-radius:8px;
  padding:16px 16px 14px;min-height:150px;position:relative;box-shadow:4px 4px 0 rgba(32,25,19,.22)}
.panel .num{position:absolute;top:-13px;left:-13px;background:var(--ink);color:var(--parch);width:28px;
  height:28px;border-radius:50%;text-align:center;line-height:28px;font-size:.85rem;border:2px solid var(--paper);z-index:3}
.panel .scene{font-style:italic;color:var(--muted);font-size:.9rem;margin-bottom:12px}
.bubble{background:#fff;border:1.5px solid var(--ink);border-radius:12px;padding:7px 12px;margin:7px 0;
  font-size:.92rem;position:relative}
.bubble b{color:var(--ox)}
.caption{margin-top:12px;font-size:.9rem;border-top:1px dashed var(--line);padding-top:8px;color:var(--ink2)}
.panel.illustrated{border-radius:8px;overflow:hidden;padding:0;box-shadow:4px 4px 0 rgba(32,25,19,.22);
  display:flex;flex-direction:column}
.panel.illustrated .art{width:100%;display:block;aspect-ratio:4/3;object-fit:cover}
.panel.illustrated .txt{background:var(--paper);padding:12px 14px 12px;display:flex;flex-direction:column;gap:2px}
.panel.illustrated .bubble{margin:4px 0;font-size:.88rem;background:#fdfaf0}
.panel.illustrated .bubble b{color:var(--ox2)}
.panel.illustrated .caption{margin-top:8px;color:var(--ink2);font-size:.84rem;border-top:1px solid var(--line);padding-top:7px}
.story-head .crumb{font-size:.82rem;color:var(--gold);letter-spacing:.08em;text-transform:uppercase;margin-bottom:8px}
/* glossary */
.gloss{columns:2;column-gap:40px}
@media(max-width:760px){.gloss{columns:1}}
.gloss .g{break-inside:avoid;margin-bottom:16px}
.gloss .g h3{font-size:.95rem;color:var(--ox);font-weight:normal;margin-bottom:6px;border-bottom:1px solid var(--line);padding-bottom:4px}
.gloss ul{padding-left:18px;font-size:.9rem}
/* people / places */
.people,.places{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:18px}
.person,.place{display:flex;flex-direction:column;gap:8px}
.person .person-head,.place .person-head{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:8px}
.person h3,.place h3{font-size:1.15rem;font-weight:normal}
.person .span,.place .span{color:var(--ox);font-size:.8rem;letter-spacing:.04em;font-variant:small-caps}
.person .role,.place .role{color:var(--gold);font-size:.82rem;letter-spacing:.05em;text-transform:uppercase}
.person p,.place p{font-size:.92rem;color:var(--ink)}
.person .ednote,.place .ednote{background:#f1e7cd;border-left:3px solid var(--gold);padding:8px 12px;border-radius:0 4px 4px 0;font-size:.9rem}
.person .ednote b,.place .ednote b{color:var(--ox)}
.person .meta,.place .meta{margin-top:auto;padding-top:6px;font-size:.85rem;color:var(--muted)}
/* timeline */
.tl-scroll{display:flex;gap:0;overflow-x:auto;padding:8px 4px 16px;scroll-snap-type:x proximity}
.tl-item{flex:0 0 176px;background:var(--paper);border:1px solid var(--line);border-top:3px solid var(--ox);border-radius:6px;padding:12px 12px;margin-right:12px;scroll-snap-align:start;
  display:flex;flex-direction:column;gap:6px;box-shadow:0 4px 10px rgba(32,25,19,.08);text-decoration:none;color:var(--ink);transition:transform .15s ease}
.tl-item:hover{transform:translateY(-3px);color:var(--ink);text-decoration:none;border-top-color:var(--gold)}
.tl-year{color:var(--ox);font-size:1.05rem;font-variant:small-caps;letter-spacing:.03em}
.tl-label{font-size:.86rem;line-height:1.35}
.tl-era{align-self:flex-start;font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:#fff;background:var(--olive);border-radius:9px;padding:1px 8px;margin-top:auto}
.tl-era:before{content:"Era "}
footer.site{border-top:1px solid var(--line);padding:28px 0;font-size:.83rem;color:var(--muted);
  margin-top:50px}
footer.site .wrap{display:flex;gap:20px;flex-wrap:wrap;justify-content:space-between}
footer.site a{color:var(--ox)}
"""

APP_JS = r"""
let PAGES=[],IDX=0;
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
async function initReader(){
  const res=await fetch('data/pages.json');const B=(await res.json());
  PAGES=B.pages;
  const q=new URLSearchParams(location.search);IDX=Math.min(Math.max(0,parseInt(q.get('p')||'0')),PAGES.length-1);
  const sel=document.getElementById('jump');const grp={};
  PAGES.forEach((pg,i)=>{const key=pg.era||'Front matter';
    const o=document.createElement('option');o.value=i;o.dataset.grp=key;
    o.textContent=(pg.p?('p.'+pg.p+' — '):'scan '+pg.n+' — ')+(pg.s||pg.k);
    sel.appendChild(o)});
  sel.addEventListener('change',()=>go(parseInt(sel.value)));
  document.getElementById('prev').onclick=()=>go(IDX-1);
  document.getElementById('next').onclick=()=>go(IDX+1);
  document.getElementById('search').oninput=onSearch;
  document.onkeydown=e=>{if(e.target.tagName==='INPUT')return;
    if(e.key==='ArrowLeft')go(IDX-1);if(e.key==='ArrowRight')go(IDX+1)};
  render();
}
function go(i){if(i<0||i>=PAGES.length)return;IDX=i;
  history.replaceState(null,'','?p='+i);render();window.scrollTo({top:0,behavior:'smooth'})}
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
  if(pg.an&&pg.an.length){h+='<div class="annots"><h3>Annotations</h3><ul>'+pg.an.map(a=>{
    const m=a.match(/^\[(.+?)\]\s*(.*)$/);
    return m?'<li><span class="tag">['+esc(m[1])+']</span> '+esc(m[2])+'</li>':'<li>'+esc(a)+'</li>'}).join('')+'</ul></div>'}
  if(pg.notes)h+='<p class="pnote">Scan note: '+esc(pg.notes)+'</p>';
  const et=document.getElementById('erae');
  if(et)et.textContent=pg.era||'';
  document.getElementById('transcript').innerHTML=h;
}
function stripTags(h){const d=document.createElement('div');d.innerHTML=h;return d.textContent}
function onSearch(e){
  const q=e.target.value.trim().toLowerCase();
  const box=document.getElementById('search-results');
  if(q.length<3){box.innerHTML='';return}
  const hits=[];
  PAGES.forEach((pg,i)=>{const txt=stripTags(pg.t||'').toLowerCase();
    const at=txt.indexOf(q);if(at>=0)hits.push({i,pg,snip:txt.slice(Math.max(0,at-55),at+120)})});
  box.innerHTML='<b>'+hits.length+' page(s) match</b>'+hits.slice(0,40).map(h=>
    '<a href="?p='+h.i+'" onclick="go('+h.i+');return false"><span class="hit-n">'+
    (h.pg.era||'Front')+' · '+(h.pg.p?('p.'+h.pg.p):'scan '+h.pg.n)+'</span> — …'+esc(h.snip)+'…</a>').join('');
}
"""


def page_html(title, nav_active, body, extra_js=""):
    nav = [("index.html", "Home"), ("reader.html", "Reader"),
           ("stories.html", "Stories"), ("people.html", "People"),
           ("places.html", "Places"), ("plates.html", "Plates"),
           ("glossary.html", "Glossary")]
    links = []
    for h, t in nav:
        cls = ' class="active"' if h.split(".")[0] == nav_active else ""
        links.append(f'<a href="{h}"{cls}>{t}</a>')
    links = "".join(links)
    js = f"<script>{APP_JS}{extra_js}</script>" if extra_js else ""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="assets/style.css">
</head><body>
<nav class="site"><div class="wrap"><span class="brand">{html.escape(book_brand)}</span>{links}</div></nav>
{body}
<footer class="site"><div class="wrap">
<span>{html.escape(book_title)} · transcribed page-by-page by vision model from the 1768 scan (Digital Library of India)</span>
<span><a href="{book_repo}">View the source repository</a></span>
</div></footer>{js}</body></html>"""


def build(book_dir, site_dir, work_dir, stories_dir):
    with open(os.path.join(book_dir, "book.json")) as f:
        book = json.load(f)
    pages = load_transcripts(os.path.join(work_dir, "transcripts"))
    stories = load_stories(stories_dir)
    people = (load_simple(os.path.join(book_dir, "people.json")) or {}).get("people", [])
    places = (load_simple(os.path.join(book_dir, "places.json")) or {}).get("places", [])
    timeline = (load_simple(os.path.join(book_dir, "timeline.json")) or {}).get("events", [])
    chapters = extract_chapters(pages, os.path.join(work_dir, "transcripts"))

    # assign era to each page + chapter
    era_of = {}
    for e in ERAS:
        for n in range(e["range_start"], e["range_end"] + 1):
            era_of[n] = e["num"]
    for p in pages:
        p["era"] = era_of.get(p["n"], "")
    for c in chapters:
        c["era"] = era_of.get(c["n"], "")
    chapters_by_era = {e["num"]: [] for e in ERAS}
    for c in chapters:
        chapters_by_era.setdefault(c["era"], []).append(c)

    print(f"{len(pages)} transcripts, {len(stories)} stories, {len(chapters)} chapters")
    os.makedirs(site_dir, exist_ok=True)
    os.makedirs(os.path.join(site_dir, "assets"), exist_ok=True)
    os.makedirs(os.path.join(site_dir, "data"), exist_ok=True)
    with open(os.path.join(site_dir, "assets", "style.css"), "w") as f:
        f.write(STYLE)
    export_images(pages, os.path.join(work_dir, "pages"), site_dir)

    with open(os.path.join(site_dir, "data", "pages.json"), "w") as f:
        json.dump({"book": book["title"], "pages": pages}, f)

    total_words = sum(p["words"] for p in pages)
    btitle = book.get("title", "History")
    bshort = " ".join(btitle.split()[:3])

    global book_brand, book_title, book_repo
    book_brand = "The History of Hindostan"
    book_title = btitle
    book_repo = "https://github.com/chinmayjoshi/history-digitizer"

    build_landing(site_dir, book, pages, chapters_by_era, stories, total_words, timeline)
    build_reader(site_dir, book, pages)
    build_stories(site_dir, book, stories)
    build_people(site_dir, book, pages, people, stories)
    build_places(site_dir, book, pages, places, stories)
    build_plates(site_dir, book, pages)
    build_glossary(site_dir, book, pages)
    print(f"site written to {site_dir}/")


def hero_body(site_dir, book, total_words, pages):
    portrait = None
    for n in (6, 7, 0):
        if any(p["n"] == n for p in pages):
            portrait = n
            break
    img = f"assets/pages/page_{portrait:04d}.jpg" if portrait is not None else ""
    return f"""
<div class="hero">
 <div class="wrap">
  <div class="portrait-frame">
    {f'<img class="portrait" src="{img}" alt="frontispiece">' if img else ''}
  </div>
  <div>
    <div class="kicker">A Digitized Manuscript · Vol. I of II · 1768</div>
    <h1>{html.escape(book.get('title',''))}</h1>
    <p class="subtitle">{html.escape(book.get('subtitle',''))}</p>
    <p class="byline">Alexander Dow, translator · from the Persian of Mahummud Casim Ferishta</p>
    <p>
      <a class="btn" href="reader.html">Begin the reader →</a>
      <a class="btn ghost" href="stories.html">The notable stories</a>
    </p>
  </div>
 </div>
</div>"""


def stats_body(total_words, pages, stories):
    plates = sum(1 for p in pages if p["plate"])
    return """<div class="stats"><div class="wrap">
  <div class="stat"><b>1768</b><span>published, London</span></div>
  <div class="stat"><b>464</b><span>pages digitized</span></div>
  <div class="stat"><b>3</b><span>eras of empire</span></div>
  <div class="stat"><b>42</b><span>chapters</span></div>
  <div class="stat"><b>8</b><span>notable stories</span></div>
  <div class="stat"><b>""" + f"{total_words//1000}k" + """</b><span>words transcribed</span></div>
</div></div>"""


def landing_story_cards(stories):
    cards = []
    order = sorted(stories, key=lambda s: s["slug"])
    for s in order:
        theme = s.get("theme", "Rise of Empires")
        cards.append(f"""
<div class="story {theme_cls(theme)}">
  <span class="tag">{THEME_ICONS.get(theme, '✧')} {html.escape(theme)}</span>
  <h3><a href="story-{html.escape(s['slug'])}.html">{html.escape(s['title'])}</a></h3>
  <p>{html.escape(s.get('hook',''))}</p>
  <div class="meta"><a href="story-{html.escape(s['slug'])}.html">Read the story →</a> · {len(s.get('panels',[]))} panels · {html.escape(s.get('pages',''))}</div>
</div>""")
    return "".join(cards)


def era_card(e, chapters):
    ch = chapters.get(e["num"], [])
    start = ch[0]["n"] if ch else e["range_start"]
    img = f"assets/pages/page_{start:04d}.jpg"
    chap_html = ""
    for c in ch:
        pg = f'<span class="pg">p.{c["title"].split()[-1]}</span>' if False else ""
        chap_html += f'<a href="reader.html?p={c["n"]}">Sect. {c["section"]} — {html.escape(c["title"][:54])}</a>'
    num = e["num"]
    return f"""
<div class="era">
  <div class="top"><img src="{img}" alt="era {num} opening">
    <div class="num">{num}</div></div>
  <div class="body">
    <div class="span">{html.escape(e['span'])}</div>
    <h3>{html.escape(e['title'])}</h3>
    <p>{html.escape(e['blurb'])}</p>
    <details class="chapter-list"><summary>{len(ch)} chapters in this era</summary>{chap_html}</details>
  </div>
</div>"""


def build_landing(site_dir, book, pages, chapters_by_era, stories, total_words, timeline=()):
    hero = hero_body(site_dir, book, total_words, pages)
    stats = stats_body(total_words, pages, stories)
    eras = "".join(era_card(e, chapters_by_era) for e in ERAS)
    story_cards = landing_story_cards(stories)
    plates = [p for p in pages if p["plate"]][:3]
    plate_html = ""
    for p in plates:
        plate_html += (f'<figure><a href="reader.html?p={pages.index(p)}">'
                       f'<img loading="lazy" src="assets/pages/page_{p["n"]:04d}.jpg"></a>'
                       f'<figcaption>{html.escape(p["platedesc"] or "Plate")}</figcaption></figure>')

    timeline_html = ""
    if timeline:
        items = "".join(
            f'<a class="tl-item" href="reader.html?p={e.get("read_scan",0)}">'
            f'<span class="tl-year">{html.escape(str(e.get("ce","")))}</span>'
            f'<span class="tl-label">{html.escape(str(e.get("label","")))}</span>'
            f'<span class="tl-era">{html.escape(str(e.get("era","")))}</span></a>'
            for e in timeline)
        timeline_html = f'<div class="tl-scroll">{items}</div>'
    body = f"""{hero}{stats}
<main><div class="wrap">
  <section class="block">
    <div class="section-head"><span class="orn">✧</span><h2>About this book</h2></div>
    <p class="lead">{html.escape(book.get('summary', "A 1768 translation of Ferishta's great Persian history of Hindostan — from the earliest Hindu kings to the Delhi Sultanate on the eve of Timur, digitized page by page and read aloud by a vision model."))}</p>
    <div class="lead"><div class="cliff">The volume closes on a held breath: \"to compleat the miseries of the unhappy city and empire, news arrived that Amir Timur had crossed the Sind, with an intention to conquer Hindostan. — END of the first Volume.\"</div></div>
  </section>
  <section class="block">
    <div class="section-head"><span class="orn">✦</span><h2>The three eras</h2><p>Choose a thread through the book</p></div>
    <div class="eras">{eras}</div>
  </section>
  <section class="block">
    <div class="section-head"><span class="orn">◷</span><h2>Across the centuries</h2><p>A chronological spine · Hijri years as Firishta gives them</p></div>
    <div class="tl">{timeline_html}</div>
  </section>
  <section class="block">
    <div class="section-head"><span class="orn">❦</span><h2>Notable stories</h2><p>The episodes that stopped us — retold with comic panels</p></div>
    <div class="story-grid">{story_cards}</div>
  </section>
  <section class="block">
    <div class="section-head"><span class="orn">♁</span><h2>Preserved plates</h2><p>The engraved relics kept whole</p></div>
    <div class="gallery">{plate_html or '<p>No plates found.</p>'}</div>
  </section>
</div></main>"""
    with open(os.path.join(site_dir, "index.html"), "w") as f:
        f.write(page_html(book.get("title", "Home"), "index", body))


def build_reader(site_dir, book, pages):
    body = """
<main><div class="wrap">
<div class="reader-bar">
  <button id="prev">← Prev</button><button id="next">Next →</button>
  <select id="jump"></select><span class="pg" id="pglabel"></span>
  <input type="search" id="search" placeholder="Search the whole book… (3+ chars)">
</div>
<div id="search-results" class="search-results"></div>
<div class="panes">
  <div class="pane scan"><h3>Original scan (1768)</h3><img id="scanimg" alt="scan"></div>
  <div class="pane text"><h3><span>Transcription &amp; annotations</span><span class="era-tag" id="erae"></span></h3><div id="transcript"></div></div>
</div>
</div></main>"""
    with open(os.path.join(site_dir, "reader.html"), "w") as f:
        f.write(page_html(f"Reader — {book.get('title','')}", "reader", body, extra_js="\ninitReader();"))


def build_stories(site_dir, book, stories):
    # group by theme, preserve canonical order
    themes = ["Origins & Legends", "Rise of Empires", "Battles & Raids",
              "Rulers of Uncommon Fate", "The Decline"]
    grouped = {}
    for s in stories:
        grouped.setdefault(s.get("theme", "Rise of Empires"), []).append(s)
    sections = []
    for th in themes:
        items = grouped.get(th, [])
        if not items:
            continue
        icon = THEME_ICONS.get(th, "✧")
        cards = ""
        for s in items:
            cards += f"""
<div class="story {theme_cls(th)}">
  <span class="tag">{icon} {html.escape(th)}</span>
  <h3><a href="story-{html.escape(s['slug'])}.html">{html.escape(s['title'])}</a></h3>
  <p>{html.escape(s.get('hook',''))}</p>
  <div class="meta">{len(s.get('panels',[]))} panels · {html.escape(s.get('pages',''))}</div>
  <p style="margin-top:10px"><a class="btn ghost" style="display:inline-block;padding:8px 14px;font-size:.82rem" href="story-{html.escape(s['slug'])}.html">Read the story →</a></p>
</div>"""
        sections.append(f"""
<div class="section-head"><span class="orn">{icon}</span><h2>{html.escape(th)}</h2></div>
<div class="story-grid">{cards}</div>""")
    body = f"""<main><div class="wrap">
  <section class="block">
    <div class="section-head"><span class="orn">❦</span><h2>Notable stories</h2>
      <p>Standout episodes from the book, summarized and retold as lightweight vector comics</p></div>
    <p class="lead" style="margin-bottom:20px">Eight moments worth stopping for — from the four ages of the world to the eve of Timur — arranged by the movement they embody.</p>
  </section>
  <section class="block">{''.join(sections)}</section>
</div></main>"""
    with open(os.path.join(site_dir, "stories.html"), "w") as f:
        f.write(page_html("Notable stories", "stories", body))

    for s in stories:
        comic_dir = os.path.join(site_dir, "assets", "comic")
        panels = ""
        for i, pn in enumerate(s.get("panels", [])):
            art = os.path.join(comic_dir, f"{s['slug']}_{i+1:02d}.jpg")
            bubbles = "".join(
                f'<div class="bubble"><b>{html.escape(d.get("speaker",""))}:</b> {html.escape(d.get("text",""))}</div>'
                for d in pn.get("dialogue", []))
            caption = f'<div class="caption">{html.escape(pn.get("caption",""))}</div>'
            num = f'<span class="num">{i+1}</span>'
            scene = f'<div class="pscene">{html.escape(pn.get("scene",""))}</div>' if not os.path.exists(art) else ""
            if os.path.exists(art):
                panels += f"""<div class="panel illustrated" id="p{i+1}">
  <span class="num">{i+1}</span>
  <img class="art" src="assets/comic/{html.escape(s['slug'])}_{i+1:02d}.jpg" alt="panel {i+1}" loading="lazy">
  <div class="txt">{bubbles}{caption}</div>
</div>"""
            else:
                panels += f"""<div class="panel" id="p{i+1}">{num}
  <div class="scene">{scene}</div>{bubbles}{caption}
</div>"""
        body = f"""<main><div class="wrap">
<section class="block story-head">
  <div class="crumb">{html.escape(s.get('theme',''))} · {html.escape(s.get('pages',''))}</div>
  <div class="section-head"><span class="orn">❦</span><h2>{html.escape(s['title'])}</h2></div>
  <p class="lead">{html.escape(s.get('hook',''))}</p>
</section>
<div class="card">{s['summary_html']}
<p style="margin-top:12px;color:var(--muted)"><b>Figures:</b> {html.escape(', '.join(s.get('figures',[])))}</p></div>
<section class="block"><div class="section-head"><span class="orn">✧</span><h2>The story as a comic</h2></div>
<div class="comic">{panels}</div></section>
<section class="block"><div class="card"><h3>Why it stands out</h3><p>{html.escape(s.get('why',''))}</p></div>
<p><a href="stories.html">← All stories</a></p></section>
</div></main>"""
        with open(os.path.join(site_dir, f"story-{s['slug']}.html"), "w") as f:
            f.write(page_html(s["title"], "stories", body))


def build_people(site_dir, book, pages, people, stories):
    if not people:
        return
    story_by_slug = {s["slug"]: s for s in stories}
    intro = "The monarchs, slave-kings, queens and conquerors who cross Firishta's pages."
    cards = []
    for p in people:
        story_links = "".join(
            f'<a href="story-{html.escape(s)}.html">featured story</a> '
            for s in p.get("stories", []) if s in story_by_slug)
        read = (f'<span class="meta">Read: '
                f'<a href="reader.html?p={p.get("read_scan",0)}">scan {p.get("read_scan","")}</a>'
                + (f" · {story_links}" if story_links else "")
                + "</span>")
        cards.append(f"""
<div class="card person">
  <div class="person-head">
    <h3>{html.escape(p.get('name',''))}</h3>
    <span class="span">{html.escape(p.get('dates',''))}</span>
  </div>
  <p class="role">{html.escape(p.get('role',''))}</p>
  <p><b>In the book:</b> {html.escape(p.get('book',''))}</p>
  <p class="ednote"><b>Editor's note:</b> {html.escape(p.get('note',''))}</p>
  {read}
</div>""")
    body = f"""<main><div class="wrap">
  <section class="block">
    <div class="section-head"><span class="orn">♛</span><h2>The people — a cast of the book</h2>
      <p>Monarchs, slave-kings, queens and conquerors crossing Firishta's pages</p></div>
    <p class="lead">{html.escape(intro)}</p>
  </section>
  <section class="block"><div class="people">{''.join(cards)}</div></section>
</div></main>"""
    with open(os.path.join(site_dir, "people.html"), "w") as f:
        f.write(page_html("People", "people", body))


def build_places(site_dir, book, pages, places, stories):
    if not places:
        return
    story_by_slug = {s["slug"]: s for s in stories}
    cards = []
    for p in places:
        story_links = "".join(
            f'<a href="story-{html.escape(s)}.html">featured story</a> '
            for s in p.get("stories", []) if s in story_by_slug)
        read = (f'<span class="meta">Read: '
                f'<a href="reader.html?p={p.get("read_scan",0)}">scan {p.get("read_scan","")}</a>'
                + (f" · {story_links}" if story_links else "")
                + "</span>")
        cards.append(f"""
<div class="card place">
  <div class="person-head">
    <h3>{html.escape(p.get('name',''))}</h3>
    <span class="span">{html.escape(p.get('modern',''))}</span>
  </div>
  <p class="role">{html.escape(p.get('role',''))}</p>
  <p><b>In the book:</b> {html.escape(p.get('book',''))}</p>
  <p class="ednote"><b>Editor's note:</b> {html.escape(p.get('note',''))}</p>
  {read}
</div>""")
    body = f"""<main><div class="wrap">
  <section class="block">
    <div class="section-head"><span class="orn">♁</span><h2>The places of the book</h2>
      <p>Cities, frontiers, rivers and holy places on Firishta's stage</p></div>
    <p class="lead">From Ghizni to Sumnat, the geography that shaped the history — with the modern settings of each.</p>
  </section>
  <section class="block"><div class="places">{''.join(cards)}</div></section>
</div></main>"""
    with open(os.path.join(site_dir, "places.html"), "w") as f:
        f.write(page_html("Places", "places", body))


def build_plates(site_dir, book, pages):
    figs = "".join(
        f'<figure><a href="reader.html?p={i}"><img loading="lazy" src="assets/pages/page_{p["n"]:04d}.jpg"></a>'
        f'<figcaption>{html.escape(p["platedesc"] or "Plate")} · scan {p["n"]}</figcaption></figure>'
        for i, p in enumerate(pages) if p["plate"])
    body = f"""<main><div class="wrap"><section class="block">
  <div class="section-head"><span class="orn">♁</span><h2>Preserved plates &amp; illustrations</h2>
    <p>The engraved relics kept whole against the text</p></div>
  <div class="gallery">{figs or '<p>None found.</p>'}</div>
</section></div></main>"""
    with open(os.path.join(site_dir, "plates.html"), "w") as f:
        f.write(page_html("Plates", "plates", body))


def build_glossary(site_dir, book, pages):
    gloss = {}
    for p in pages:
        for a in p["an"]:
            m = re.match(r"^\[(.+?)\]\s*(.*)$", a)
            if m:
                gloss.setdefault(m.group(1), []).append(
                    f'{m.group(2)} <a href="reader.html?p={p["n"]}">(scan {p["n"]})</a>')
    ghtml = ""
    for tag, items in sorted(gloss.items()):
        lis = "".join(f"<li>{x}</li>" for x in items[:120])
        ghtml += f'<div class="g"><h3>[{html.escape(tag)}] · {len(items)}</h3><ul>{lis}</ul></div>'
    body = f"""<main><div class="wrap"><section class="block">
  <div class="section-head"><span class="orn">❧</span><h2>Glossary &amp; editorial annotations</h2>
    <p>Aggregated from the margin of every page, grouped by tag</p></div>
  <div class="gloss">{ghtml or '<p>None yet.</p>'}</div>
</section></div></main>"""
    with open(os.path.join(site_dir, "glossary.html"), "w") as f:
        f.write(page_html("Glossary", "glossary", body))


def main():
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
