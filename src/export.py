"""Export OCR results to plain text, markdown, or JSON."""
from __future__ import annotations

import json
from pathlib import Path


def to_text(pages: list[str]) -> str:
    return "\n\n".join(f"--- Page {i + 1} ---\n{text}" for i, text in enumerate(pages))


def to_markdown(pages: list[str]) -> str:
    lines = []
    for i, text in enumerate(pages):
        lines.append(f"## Page {i + 1}\n\n{text.strip()}")
    return "\n\n".join(lines)


def save(pages: list[str], output_dir: str, fmt: str = "txt") -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        path = out / "pages.json"
        path.write_text(json.dumps(pages, indent=2))
    elif fmt == "md":
        path = out / "export.md"
        path.write_text(to_markdown(pages))
    else:
        path = out / "export.txt"
        path.write_text(to_text(pages))
    return path
