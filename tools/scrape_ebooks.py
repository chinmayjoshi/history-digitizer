"""Scrape the Indian Culture e-books catalogue into a SQLite database.

Pulls all pages of the /rest-v1/ebooks API (55k+ books) and stores a
normalized catalogue. Caches each raw API page so runs can resume.

Usage:
    python3 tools/scrape_ebooks.py [--pages N] [--delay 0.4]
"""
from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import sys
import time
import urllib.request

BASE = "https://icvtestingold.nvli.in/rest-v1/ebooks"
CACHE_DIR = "cache/ebooks"
DB_PATH = "data/ebooks.db"
CREDS = base64.b64encode(b"VipulAPI:V!PuL@212").decode()

# Fields that matter for a level-1 catalogue. Values are the API keys.
FIELD_MAP = {
    "nid": "nid",
    "title": "title",
    "author": "field_dc_contributor_author",
    "editor": "field_dc_contributor_editor",
    "translator": "field_dc_contributor_translator",
    "compiler": "field_dc_contributr_compiler",
    "director": "field_dc_contributor_director",
    "year_copyright": "field_dc_date_copyright",
    "year_issued": "field_dc_date_issued",
    "pages": "field_dc_format_extent",
    "accession_no": "field_dc_identifier_accessionnum",
    "class_no": "field_dc_identifier_classnumber",
    "volume_no": "field_dc_identifier_volumenumber",
    "language": "field_dc_language_iso",
    "publisher": "field_dc_publisher",
    "series": "field_dc_relation_ispartofseries",
    "source": "field_dc_source",
    "subject": "field_dc_subject",
    "type": "field_dc_type",
    "library": "field_parent_library_name",
    "collection": "field_parent_collection_in_dspac",
    "pdf_path": "field_pdf_digital_file",
    "ocr_summary": "field_ocr_summary",
    "description": "field_dc_description",
    "spatial": "field_dc_coverage_spatial",
    "temporal": "field_dc_coverage_temporal",
}


def clean(v) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    return v if v else None


def fetch(url: str, retries: int = 4) -> dict:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"Authorization": f"Basic {CREDS}", "User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))


def get_pager_info() -> dict:
    return fetch(BASE + "?page=0")["pager"]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS books (
            nid TEXT PRIMARY KEY,
            title TEXT,
            author TEXT,
            editor TEXT,
            translator TEXT,
            compiler TEXT,
            director TEXT,
            year_copyright TEXT,
            year_issued TEXT,
            pages TEXT,
            accession_no TEXT,
            class_no TEXT,
            volume_no TEXT,
            language TEXT,
            publisher TEXT,
            series TEXT,
            source TEXT,
            subject TEXT,
            type TEXT,
            library TEXT,
            collection TEXT,
            pdf_path TEXT,
            ocr_summary TEXT,
            description TEXT,
            spatial TEXT,
            temporal TEXT,
            raw JSON
        );
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )


def normalize(row: dict) -> dict:
    out = {}
    for col, key in FIELD_MAP.items():
        out[col] = clean(row.get(key))
    out["raw"] = json.dumps(row, ensure_ascii=False)
    return out


def upsert(conn: sqlite3.Connection, norm: dict) -> None:
    cols = list(FIELD_MAP.keys()) + ["raw"]
    placeholders = ",".join("?" for _ in cols)
    sql = (
        "INSERT OR REPLACE INTO books ({cols}) VALUES ({ph})"
    ).format(cols=",".join(cols), ph=placeholders)
    conn.execute(sql, [norm[c] for c in cols])


def download_pages(max_pages: int, workers: int, delay: float) -> None:
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    os.makedirs(CACHE_DIR, exist_ok=True)
    pager = get_pager_info()
    total_pages = pager["total_pages"]

    missing = [
        p
        for p in range(total_pages)
        if not os.path.exists(f"{CACHE_DIR}/page_{p}.json")
    ]
    if max_pages is not None:
        missing = missing[: max_pages - (total_pages - len(missing))]

    start = time.time()
    done = 0
    lock_errors = []

    def work(page: int):
        cache_file = f"{CACHE_DIR}/page_{page}.json"
        try:
            data = fetch(f"{BASE}?page={page}")
            with open(cache_file, "w") as f:
                json.dump(data, f)
            return page, None
        except Exception as e:  # noqa: BLE001
            return page, str(e)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(work, p): p for p in missing}
        for fut in as_completed(futures):
            page, err = fut.result()
            done += 1
            if err:
                lock_errors.append((page, err))
                print(f"[!] page {page} FAILED: {err}", flush=True)
            if done % 25 == 0 or done == len(missing):
                rate = done / max(time.time() - start, 0.001)
                print(
                    f"downloaded {done}/{len(missing)} rate={rate:.2f}/s "
                    f"eta={(len(missing)-done)/rate/60:.1f}min",
                    flush=True,
                )
            time.sleep(delay)

    print(f"Download phase done. failures: {len(lock_errors)}")


def build_db() -> None:
    import glob
    import os

    os.makedirs("data", exist_ok=True)
    for page_file in sorted(
        glob.glob(f"{CACHE_DIR}/page_*.json"),
        key=lambda f: int(os.path.basename(f).split("_")[1].split(".")[0]),
    ):
        with open(page_file) as f:
            data = json.load(f)
        conn = sqlite3.connect(DB_PATH)
        ensure_schema(conn)
        for row in data.get("results", []):
            upsert(conn, normalize(row))
        conn.commit()
        conn.close()
    print(f"DB built at {DB_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=None, help="Max pages to fetch")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay between requests")
    parser.add_argument("--workers", type=int, default=6, help="Concurrent downloads")
    parser.add_argument("--build-only", action="store_true", help="Only rebuild DB from cache")
    parser.add_argument("--download-only", action="store_true", help="Only download pages")
    args = parser.parse_args()

    if not args.build_only:
        download_pages(args.pages, args.workers, args.delay)
    if not args.download_only:
        build_db()

    pager = get_pager_info()
    total = pager["total_results"]
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('total_results',?)", (str(total),)
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('scraped_at',?)",
        (time.strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()
    print(f"DONE. Books stored: {count} / {total}")
    conn.close()


if __name__ == "__main__":
    main()
