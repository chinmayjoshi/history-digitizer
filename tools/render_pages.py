"""Generic PDF page renderer. Renders PDF pages to PNG images that can be
read by a vision model for transcription.

Usage:
    python3 tools/render_pages.py input/book.pdf work/pages [--dpi 150] [--pages 0-20]
"""
from __future__ import annotations

import argparse
import os

import fitz


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("outdir")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--pages", default=None, help="e.g. 0-20 or 5,7,9")
    parser.add_argument("--fmt", default="png", choices=["png", "jpg"])
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    doc = fitz.open(args.pdf)
    zoom = args.dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    if args.pages:
        import re

        idx = []
        for part in args.pages.split(","):
            if "-" in part:
                a, b = part.split("-")
                idx.extend(range(int(a), int(b) + 1))
            else:
                idx.append(int(part))
    else:
        idx = range(doc.page_count)

    ext = args.fmt
    for i in idx:
        page = doc[i]
        pix = page.get_pixmap(matrix=mat)
        out = os.path.join(args.outdir, f"page_{i:04d}.{ext}")
        pix.save(out)
        print(f"rendered {out} {pix.width}x{pix.height}", flush=True)


if __name__ == "__main__":
    main()
