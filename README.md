# History Digitizer

Tools for analyzing and digitizing scanned history books — from catalogue-scale
metadata scraping to full vision-model transcription of individual books into
rich static reading experiences. See `docs/SPEC.md` for the design.

## What's here

- `data/` (git-ignored) — level-1 catalogue of ~22k e-books from
  indianculture.gov.in (`ebooks.db` + CSV export)
- `tools/scrape_ebooks.py` — resumable API scraper for the catalogue
- `tools/render_pages.py` — render PDF pages to PNGs (PyMuPDF)
- `tools/transcribe.py` — vision-model transcription of page scans to
  annotated markdown (OpenRouter multimodal, concurrent, resumable)
- `tools/extract_plates.py` — preserve pages containing plates/illustrations
- `tools/build_site.py` — build the static reading site (reader, search,
  plates gallery, notable stories + comics, glossary)
- `history-of-hindostan/` — first book: Dow's 1768 translation of Ferishta,
  *History of Hindostan*, Vol. I (manifest, stories, built site)
- `src/` — earlier Tesseract-based OCR pipeline (superseded by the vision
  pipeline for old print, kept for reference)

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Requires Tesseract only for the legacy `src/` pipeline:

```bash
brew install tesseract
```

## Usage (rich book pipeline)

```bash
python3 tools/render_pages.py input/book.pdf work/pages
python3 tools/transcribe.py <book-slug> --workers 12
python3 tools/extract_plates.py <book-dir>
python3 tools/build_site.py <book-dir> --site-dir docs
```

Place your scanned source files in `input/` (git-ignored).
