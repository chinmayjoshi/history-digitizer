"""Preprocess scanned pages to improve OCR accuracy."""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def _to_cv(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def _to_pil(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))


def deskew(img: Image.Image) -> Image.Image:
    """Correct page skew using minAreaRect on detected text."""
    arr = _to_cv(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    gray = cv2.bitwise_not(gray)
    coords = np.column_stack(np.where(gray > 0))
    if coords.size == 0:
        return img
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.5:
        return img
    h, w = arr.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rotated = cv2.warpAffine(arr, m, (w, h), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_REPLICATE)
    return _to_pil(rotated)


def binarize(img: Image.Image, block_size: int = 31) -> Image.Image:
    """Apply adaptive thresholding to remove background noise."""
    arr = _to_cv(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, block_size, 15)
    return Image.fromarray(thresh)


def preprocess(img: Image.Image, do_deskew: bool = True) -> Image.Image:
    """Full preprocessing: deskew then binarize."""
    if do_deskew:
        img = deskew(img)
    return binarize(img)
