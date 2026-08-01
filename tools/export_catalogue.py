"""Export the ebooks catalogue DB to a CSV file.

Usage:
    python3 tools/export_catalogue.py [output.csv]
"""
from __future__ import annotations

import csv
import sqlite3
import sys

DB_PATH = "data/ebooks.db"
OUT = sys.argv[1] if len(sys.argv) > 1 else "data/ebooks_catalogue.csv"

COLS = [
    "nid", "title", "author", "editor", "translator", "year_copyright",
    "year_issued", "pages", "accession_no", "class_no", "volume_no",
    "language", "publisher", "series", "source", "subject", "type",
    "library", "collection", "pdf_path", "description",
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT {','.join(COLS)} FROM books ORDER BY library, title")
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r[c] for c in COLS})
    print(f"Exported {OUT}")


if __name__ == "__main__":
    main()
