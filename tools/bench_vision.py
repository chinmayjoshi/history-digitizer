"""Benchmark cheap vision-to-text models on a scanned book.

Method:
  1. Render a handful of representative page scans.
  2. Transcribe each page with a strong 'gold' reader model (ground truth).
  3. Transcribe the same pages with each candidate cheap model.
  4. A strong 'judge' model scores each candidate against the gold and
     returns a JSON verdict. Print a cost/quality table.

Usage:
    python3 tools/bench_vision.py noble-voyage/input/book.pdf \
        [--pages 30,55,90,130] \
        [--gold google/gemini-2.5-pro] \
        [--judge anthropic/claude-sonnet-4.6] \
        [--candidates a,b,c] \
        [--book "..."] [--dpi 150] [--workers 4]

Auth: OPENROUTER_API_KEY env, else opencode's auth.json key.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import fitz

API_URL = "https://openrouter.ai/api/v1/chat/completions"

TRANS_PROMPT = """You are transcribing one scanned page (verbatim) from this book:
"{book}"

Rules:
- Render the long s (ƒ) as a modern "s". Keep ALL other original spelling, capitalization, italics (as *...*), punctuation, and paragraph breaks.
- Keep running headers as [header] ..., footnotes at the very end, and printed page number if visible.
- Do not summarize or modernize. Transcribe the running text exactly.
Return ONLY the transcription plaintext, no commentary."""

JUDGE_PROMPT = """You are scoring how faithfully an OCR/vision transcription reproduces a reference.

REFERENCE (supposedly accurate) begins:
---REF---
{gold}
---REF---

CANDIDATE transcription begins:
---CAND---
{candidate}
---CAND---

Score the candidate 0-10 on:
 - Word-for-word fidelity to the reference
 - Preserved spelling/capitalization/punctuation
 - Preserved paragraph/line structure
 - Missing or hallucinated words

Reply with STRICT JSON only: {{"score": <0-10 int>, "errors": <int>, "notes": "<short>"}}"""


def get_key() -> str:
    k = os.environ.get("OPENROUTER_API_KEY")
    if k:
        return k
    return json.load(open(os.path.expanduser("~/.local/share/opencode/auth.json")))["openrouter"]["key"]


class Call:
    def __init__(self, key, model):
        self.key, self.model = key, model

    def __call__(self, text, image_b64=None):
        content = []
        if image_b64:
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"}})
        content.append({"type": "text", "text": text})
        body = {"model": self.model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.0}
        req = urllib.request.Request(
            API_URL, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://github.com/history-digitizer", "X-Title": "bench"})
        with urllib.request.urlopen(req, timeout=240) as r:
            d = json.load(r)
        usage = d.get("usage", {})
        text_out = d["choices"][0]["message"]["content"]
        if isinstance(text_out, list):
            text_out = "".join(x.get("text", "") for x in text_out if x.get("type") == "text")
        return text_out, usage


def render_pages(pdf, indexes, dpi):
    doc = fitz.open(pdf)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    imgs = {}
    for i in indexes:
        if i < doc.page_count and doc[i].get_text().strip() == "":
            imgs[i] = base64.b64encode(doc[i].get_pixmap(matrix=mat).tobytes("png")).decode()
    return imgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", default="30,55,90,130")
    ap.add_argument("--gold", default="google/gemini-2.5-pro")
    ap.add_argument("--judge", default="anthropic/claude-sonnet-4.6")
    ap.add_argument("--candidates", default="google/gemini-2.5-flash-lite,google/gemini-3.1-flash-lite,google/gemini-2.5-flash,qwen/qwen3-vl-8b-instruct")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--book", default="A Voyage to the East Indies (Noble, 1748)")
    args = ap.parse_args()

    key = get_key()
    gold = Call(key, args.gold)
    judge = Call(key, args.judge)
    cands = [Call(key, m) for m in args.candidates.split(",")]

    idxs = [int(x) for x in args.pages.split(",")]
    imgs = render_pages(args.pdf, idxs, args.dpi)
    print(f"rendered {len(imgs)} text pages: {sorted(imgs)}", flush=True)
    if not imgs:
        print("no text pages found; nobody to bench."); return

    # gold transcriptions
    print("generating gold (reference) transcriptions…", flush=True)
    golds = {}
    for i, b in imgs.items():
        t, _ = gold(TRANS_PROMPT.format(book=args.book), b)
        golds[i] = t
        print(f"  gold page {i}: {len(t)} chars", flush=True)

    # candidate transcriptions (parallel)
    print("transcribing with candidates…", flush=True)
    results = {c.model: {} for c in cands}
    for c in cands:
        print(f"  {c.model}", flush=True)

        def work(item):
            i, b = item
            t, usage = c(TRANS_PROMPT.format(book=args.book), b)
            return i, t, usage
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for i, t, usage in ex.map(work, list(imgs.items())):
                results[c.model][i] = t
                c.cost_sum = getattr(c, "cost_sum", 0.0) + usage.get("cost", 0.0)

    # judge
    print("judging fidelity…", flush=True)
    table = []
    for c in cands:
        scores = []
        for i in imgs:
            g = golds[i]; cand = results[c.model][i]
            verdict, _ = judge(JUDGE_PROMPT.format(gold=g[:1500], candidate=cand[:1500]))
            try:
                v = json.loads(re.sub(r"^```(json)?|```$", "", verdict.strip(), flags=re.M))
                scores.append(int(v.get("score", 0)))
            except Exception:
                m = re.search(r"\"score\"\s*:\s*(\d+)", verdict)
                scores.append(int(m.group(1)) if m else 0)
        avg = round(sum(scores) / len(scores), 1)
        cost_page = getattr(c, "cost_sum", 0.0) / max(len(scores), 1)
        table.append((c.model, avg, cost_page, cost_page * 389))
        print(f"    -> scored {avg}/10, ${cost_page:.4f}/page, ~${cost_page*389:.2f} full-book", flush=True)

    print("\n===== RESULTS (avg fidelity 0-10, cost) =====")
    for m, score, cp, full in sorted(table, key=lambda x: (-x[1], x[2])):
        print(f"  {score:>5}/10  {cp:8.4f}/page  {full:6.2f} full  {m}")


if __name__ == "__main__":
    main()
