"""Load scanned book pages from PDFs or image directories."""
from __future__ import annotations

from pathlib import Path
from typing import List

from PIL import Image

SUPPORTED_IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def load_images_from_dir(dir_path: str) -> List[Image.Image]:
    """Return all supported images in a directory, sorted by name."""
    image_paths = sorted(
        p for p in Path(dir_path).iterdir() if p.suffix.lower() in SUPPORTED_IMAGES
    )
    return [Image.open(p).convert("RGB") for p in image_paths]


def load_pdf(pdf_path: str) -> List[Image.Image]:
    """Convert every page of a PDF into PIL images."""
    from pdf2image import convert_from_path

    return [p.convert("RGB") for p in convert_from_path(pdf_path)]


def load_input(path: str) -> List[Image.Image]:
    """Load a PDF or image directory into a list of page images."""
    p = Path(path)
    if p.is_dir():
        return load_images_from_dir(path)
    if p.suffix.lower() == ".pdf":
        return load_pdf(path)
    return [Image.open(p).convert("RGB")]
