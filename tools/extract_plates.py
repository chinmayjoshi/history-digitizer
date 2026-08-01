"""Preserve plates/illustrations. Scans transcripts for pages flagged
contains_plate and copies the full-resolution scan image into work/plates/.
Also refreshes the plates list in the book manifest.

Usage:
    python3 tools/extract_plates.py history-of-hindostan
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil

from build_site import parse_frontmatter


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("book_dir")
    ap.add_argument("--work-dir", default="work")
    args = ap.parse_args()

    plates_dir = os.path.join(args.work_dir, "plates")
    os.makedirs(plates_dir, exist_ok=True)

    plates = []
    for path in sorted(glob.glob(os.path.join(args.work_dir, "transcripts", "page_*.md"))):
        with open(path) as f:
            meta, body = parse_frontmatter(f.read())
        if meta.get("contains_plate") != "true":
            continue
        idx = int(re.search(r"page_(\d+)\.md$", path).group(1))
        m = re.search(r"## Plate / Illustration\s*\n+(.*?)(\n## |\Z)", body, re.S)
        desc = m.group(1).strip() if m else ""
        dst = None
        for ext in ("png", "jpg"):
            src = os.path.join(args.work_dir, "pages", f"page_{idx:04d}.{ext}")
            if os.path.exists(src):
                dst = os.path.join(plates_dir, f"plate_{idx:04d}.{ext}")
                if not os.path.exists(dst):
                    shutil.copyfile(src, dst)
                break
        plates.append({"scan_page": idx, "printed_page": meta.get("printed_page"),
                       "description": desc, "image": dst})
        print(f"plate @ scan {idx}: {desc[:90]}")

    manifest_path = os.path.join(args.book_dir, "book.json")
    with open(manifest_path) as f:
        book = json.load(f)
    book["plates"] = plates
    with open(manifest_path, "w") as f:
        json.dump(book, f, indent=2)
    print(f"{len(plates)} plates preserved -> {plates_dir}, manifest updated")


if __name__ == "__main__":
    main()
