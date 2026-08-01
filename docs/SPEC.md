# Rich Book Experience — Specification

A generic, reusable framework for turning scanned historical books into
interactive, richly-annotated digital experiences hosted on static sites
(GitHub Pages). First implementation: **Davies (Alexander Dow), *History of
Hindostan to the Death of Akbar* (1768), Vol. I — Salar Jung / Digital Library
of India scan.**

## 1. Goals

1. **Faithful digitization** — render a scanned book to text by *reading each
   page image with a vision model* (not a brittle PDF text extractor).
2. **Preserve the artifact** — keep original plates, illustrations and layout
   as images alongside the text.
3. **Layer meaning** — add human-grade annotations: editorial notes, glossary,
   references, translation of archaic terms, historical context.
4. **Surfaces the notable** — a dedicated section of standout stories, each
   summarized and enriched with generated comic panels.
5. **Reusable** — everything is driven by a per-book manifest, so any other
   book in the catalogue (we already have 22,104) can be added with zero code
   changes.
6. **Static-first** — a zero-backend site that builds fully offline and
   deploys to GitHub Pages.

## 2. Pipeline Overview

```
input/<book>.pdf
   │  tools/render_pages.py (PyMuPDF)         → work/pages/page_NNNN.png
   ▼
work/pages/page_NNNN.png
   │  vision model reads image (tool: read)
   │  agent writes frontmatter + markdown     → work/transcripts/page_NNNN.md
   ▼
work/transcripts/page_NNNN.md   (YAML frontmatter + body + annotation blocks)
   │  agent flags plates → work/plates/ (saved copies) + story candidates
   ▼
book.json  (manifest: title, metadata, page order, plates, stories, glossary)
   │  tools/build_site.py (generic builder)
   ▼
site/  →  static HTML/CSS/JS  →  push to GitHub Pages
```

## 3. Transcription Convention (per page)

Each `work/transcripts/page_NNNN.md`:

```markdown
---
page: 12
book: history-of-hindostan
section: "Book I, CHAP. I"
kind: text            # text | plate | blank | map
contains_plate: false
notes: "Faded top margin, partially illegible."
---

# Page 12

## Transcription

(verbatim text as read from the scan, preserving paragraph breaks)

## Plate / Illustration
(if contains_plate: description + saved image reference)

## Annotations
- **[archaic]** "Google" → gate, valve (Persian origin)
- **[fact-check]** Ferishta's dates differ from modern scholarship by ~1 yr
```

Annotation tags (extensible): `[archaic]`, `[translation]`, `[fact]`,
`[context]`, `[correction]`, `[place]`, `[person]`, `[date]`, `[plate]`,
`[question]`.

## 4. Book Manifest (`book.json`)

Single source of truth. Includes book metadata (from our catalogue DB), page
order, list of plates, notable stories, glossary, and reading progress. The
site builder consumes only this file + the transcript folder.

## 5. Notable Stories + Comics

`stories/` — one markdown per standout story:
- summary (plain language), source pages, key figures, why it stands out
- **comic** : a script of panels → composer emits styled `<panel>` HTML/SVG
  (caption + speech bubbles + simple vector scene). No image generation;
  visual style is typographic/vector comic so it works offline and stays
  lightweight.

## 6. Site (static, GitHub Pages)

Structure:
```
site/
  index.html          — landing: cover, metadata, reader entry
  reader.html         — book reader: page nav, side-by-side scan + transcript
  stories.html        — notable stories + comics index
  story/<slug>.html   — individual story w/ comic
  plates.html         — gallery of preserved plates
  glossary.html       — terms
  assets/style.css, app.js
```
Design: parchment-era theme, serif type, dual-pane reader (original scan image
+ transcription), keyboard/next-page navigation, search within transcript.

## 7. Reuse Model

New book = drop PDF → render → transcribe → fill manifest. No code change.
`tools/` are book-agnostic. A future `level 2` could batch-drive transcription
through the vision model for the entire 22,104-book catalogue.

## 8. Roadmap/Phases

- [x] Catalogue DB (level 1) — done
- [ ] Render + pipeline tooling
- [ ] Spec (this doc)
- [ ] Transcribe Vol. I page-by-page (vision)
- [ ] Plates preservation
- [ ] Notable stories + comics
- [ ] Static site + GitHub Pages
- [ ] Review & polish pass
