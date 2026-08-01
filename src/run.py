"""CLI entry point for the digitization pipeline."""
from __future__ import annotations

import argparse

from . import export, ingest, ocr, preprocess


def main() -> None:
    parser = argparse.ArgumentParser(description="Digitize scanned history books")
    parser.add_argument("--input", required=True, help="Path to PDF or image directory")
    parser.add_argument("--output", default="output", help="Output directory")
    parser.add_argument("--lang", default="eng", help="Tesseract language code")
    parser.add_argument(
        "--format", default="txt", choices=["txt", "md", "json"], help="Export format"
    )
    parser.add_argument("--no-deskew", action="store_true", help="Skip deskewing")
    args = parser.parse_args()

    pages = ingest.load_input(args.input)
    texts: list[str] = []
    for i, page in enumerate(pages):
        processed = preprocess.preprocess(page, do_deskew=not args.no_deskew)
        texts.append(ocr.extract_text(processed, lang=args.lang))
        print(f"Processed page {i + 1}/{len(pages)}")

    path = export.save(texts, args.output, args.format)
    print(f"Exported {len(texts)} pages to {path}")


if __name__ == "__main__":
    main()
