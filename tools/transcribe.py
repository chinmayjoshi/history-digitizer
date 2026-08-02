"""Generic vision transcription tool. Reads rendered page PNGs with a
multimodal LLM (via OpenRouter) and writes per-page markdown transcripts
following docs/SPEC.md conventions.

Resumable: pages with an existing transcript are skipped.

Usage:
    python3 tools/transcribe.py <book-dir-or-slug> \
        --pages-dir work/pages --out-dir work/transcripts \
        [--model google/gemini-2.5-flash] [--workers 6] [--pages 0-20] [--limit 5]

It reads the book title/context from <book>/book.json (falling back to the
slug) and feeds it to the vision model so the prompt is book-agnostic.

Auth: OPENROUTER_API_KEY env var, else falls back to the key stored in
opencode's auth.json.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://openrouter.ai/api/v1/chat/completions"

PROMPT = """You are transcribing ONE scanned page VERBATIM from this book:

Title: {book_title}
About: {book_desc}

Verbatim transcription rules:
- The book uses the long s (ƒ). Render it as a modern lowercase "s"
  (e.g. "ſucceſs" -> "success"). Keep all other original spelling,
  capitalization, italics (mark with *...*), and punctuation.
- Preserve paragraph breaks. Render chapter/section headings as markdown
  headings (## / ### as appropriate). Keep footnotes at the bottom, prefixed
  with the original footnote marker.
- If there is a running header, include it as the first line prefixed
  with "[header] ". If a catchword appears at the bottom right, include it
  as "[catchword] X". Record the printed page number if visible.
- Do NOT translate, summarize, or modernize anything else.

Then respond with STRICT JSON only (no markdown fences), with this schema:
{{
  "kind": "text|contents|title|frontmatter|plate|map|blank",
  "printed_page": "<printed page number as string, or null>",
  "heading": "<chapter/section heading on this page, or null>",
  "transcription": "<the verbatim markdown transcription, empty string if blank>",
  "contains_plate": false,
  "plate_description": "<if contains_plate: what the illustration shows, else null>",
  "annotations": ["<0-5 short notes; tag each [archaic]/[place]/[person]/[date]/[fact]/[context]/[correction]>"],
  "notes": "<legibility/scan issues, or null>"
}}"""


def load_book_context(slug_or_dir: str) -> tuple[str, str]:
    """Return (title, description) for the book, reading book.json if the
    argument is a directory, else using the slug as the title."""
    if os.path.isdir(slug_or_dir):
        bj = os.path.join(slug_or_dir, "book.json")
        if os.path.exists(bj):
            with open(bj) as f:
                b = json.load(f)
            return b.get("title", slug_or_dir), (b.get("subtitle") or b.get("summary") or "")[:400]
    return slug_or_dir, ""


def get_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    auth = os.path.expanduser("~/.local/share/opencode/auth.json")
    with open(auth) as f:
        return json.load(f)["openrouter"]["key"]


def page_indices(pages_dir: str, pages_arg: str | None, limit: int | None) -> list[int]:
    found = sorted(
        int(m.group(1))
        for f in os.listdir(pages_dir)
        if (m := re.match(r"page_(\d+)\.(png|jpg)$", f))
    )
    if pages_arg:
        wanted: set[int] = set()
        for part in pages_arg.split(","):
            if "-" in part:
                a, b = part.split("-")
                wanted.update(range(int(a), int(b) + 1))
            else:
                wanted.add(int(part))
        found = [i for i in found if i in wanted]
    if limit:
        found = found[:limit]
    return found


def transcribe_page(client_key: str, model: str, slug: str, book_title: str,
                    book_desc: str, pages_dir: str, out_dir: str, idx: int,
                    attempts: int = 4) -> tuple[int, bool, str]:
    out_path = os.path.join(out_dir, f"page_{idx:04d}.md")
    if os.path.exists(out_path):
        return idx, True, "cached"
    for ext in ("png", "jpg"):
        img_path = os.path.join(pages_dir, f"page_{idx:04d}.{ext}")
        if os.path.exists(img_path):
            break
    else:
        return idx, False, "no image"
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    mime = "image/png" if img_path.endswith(".png") else "image/jpeg"
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT.format(book_title=book_title, book_desc=book_desc)},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {client_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/history-digitizer",
            "X-Title": "history-digitizer",
        },
    )
    delay = 2.0
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.load(resp)
            text = data["choices"][0]["message"]["content"].strip()
            # strip accidental markdown fences
            text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
            rec = json.loads(text)
            md = render_markdown(slug, idx, rec)
            with open(out_path, "w") as f:
                f.write(md)
            return idx, True, "ok"
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                json.JSONDecodeError, KeyError) as e:
            if attempt == attempts - 1:
                return idx, False, f"{type(e).__name__}: {e}"
            time.sleep(delay)
            delay *= 2
    return idx, False, "unreachable"


def render_markdown(slug: str, idx: int, rec: dict) -> str:
    # Normalize any residual long-s (U+017F) to plain 's'
    def norm(v):
        if isinstance(v, str):
            return v.replace("\u017F", "s")
        return v
    rec = {k: (norm(v) if k != "annotations" else v) for k, v in rec.items()}
    if rec.get("annotations"):
        rec["annotations"] = [norm(a) for a in rec["annotations"]]
    def esc(v):
        return str(v).replace('"', '\\"') if v is not None else ""
    lines = ["---",
             f"page: {idx}",
             f"book: {slug}",
             f"kind: {rec.get('kind', 'text')}",
             f"printed_page: \"{esc(rec.get('printed_page'))}\"",
             f"section: \"{esc(rec.get('heading'))}\"",
             f"contains_plate: {'true' if rec.get('contains_plate') else 'false'}",
             f"notes: \"{esc(rec.get('notes'))}\"",
             "---", ""]
    if rec.get("transcription"):
        lines += ["## Transcription", "", rec["transcription"].strip(), ""]
    if rec.get("contains_plate") and rec.get("plate_description"):
        lines += ["## Plate / Illustration", "", rec["plate_description"].strip(), ""]
    if rec.get("annotations"):
        lines += ["## Annotations", ""]
        lines += [f"- {a}" for a in rec["annotations"]]
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("book_dir")
    ap.add_argument("--pages-dir", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--model", default="google/gemini-2.5-flash")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--pages", default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    title, desc = load_book_context(args.book_dir)
    base_dir = args.book_dir if os.path.isdir(args.book_dir) else "."
    pages_dir = args.pages_dir or os.path.join(base_dir, "work", "pages")
    out_dir = args.out_dir or os.path.join(base_dir, "work", "transcripts")
    os.makedirs(out_dir, exist_ok=True)
    key = get_api_key()
    indices = page_indices(pages_dir, args.pages, args.limit)
    todo = [i for i in indices
            if not os.path.exists(os.path.join(out_dir, f"page_{i:04d}.md"))]
    print(f"{len(indices)} pages selected, {len(todo)} to transcribe "
          f"({len(indices) - len(todo)} cached) for {title}", flush=True)
    if not todo:
        return

    done = failed = 0
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(transcribe_page, key, args.model, os.path.basename(args.book_dir.rstrip("/")),
                          title, desc, pages_dir, out_dir, i): i for i in todo}
        for fut in cf.as_completed(futs):
            idx, ok, msg = fut.result()
            if ok:
                done += 1
            else:
                failed += 1
                print(f"FAIL page {idx}: {msg}", flush=True)
            total = done + failed
            if total % 10 == 0 or total == len(todo):
                rate = total / (time.time() - t0)
                eta = (len(todo) - total) / rate if rate else 0
                print(f"progress {total}/{len(todo)} ok={done} fail={failed} "
                      f"rate={rate:.2f}/s eta={eta/60:.1f}min", flush=True)
    print(f"done: {done} ok, {failed} failed in {(time.time()-t0)/60:.1f} min",
          flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
