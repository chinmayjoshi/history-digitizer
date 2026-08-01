"""OCR extraction using Tesseract."""
from __future__ import annotations

import pytesseract
from PIL import Image


def extract_text(page: Image.Image, lang: str = "eng") -> str:
    """Extract text from a single page image."""
    return pytesseract.image_to_string(page, lang=lang)


def extract_data(page: Image.Image, lang: str = "eng") -> dict:
    """Extract word/line-level OCR data with confidence scores."""
    return pytesseract.image_to_data(page, lang=lang, output_type=pytesseract.Output.DICT)
