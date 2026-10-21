"""
Upload handling. Whatever comes in, what gets stored is a de-identified 8-bit PNG plus a
short whitelist of technical attributes. The original file is not kept.

DICOM headers carry patient name, ID, birth date, institution and more. Instead of trying
to delete every identifying tag (a blacklist always misses one), only the attributes in
KEEP_DICOM are copied out. Everything else is dropped with the original bytes.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field

import numpy as np
import pydicom
from django.core.files.base import ContentFile
from PIL import Image as PILImage
from pydicom.errors import InvalidDicomError

MAX_PIXELS = 100_000_000  # refuse decompression bombs

KEEP_DICOM = (
    "Modality",
    "BodyPartExamined",
    "PhotometricInterpretation",
    "Rows",
    "Columns",
    "BitsStored",
    "PixelSpacing",
    "SliceThickness",
    "WindowCenter",
    "WindowWidth",
    "Manufacturer",
)


class UploadRejected(Exception):
    pass


@dataclass
class ProcessedImage:
    png: ContentFile
    width: int
    height: int
    source_format: str
    sha256: str
    metadata: dict = field(default_factory=dict)


def process_upload(data: bytes, filename: str) -> ProcessedImage:
    digest = hashlib.sha256(data).hexdigest()
    if _looks_like_dicom(data):
        array, metadata = _read_dicom(data)
        img = PILImage.fromarray(array)
        fmt = "dicom"
    else:
        img, fmt = _read_raster(data)
        metadata = {}

    buf = io.BytesIO()
    img.save(buf, format="PNG")  # re-encoding also drops EXIF and other embedded metadata
    return ProcessedImage(
        png=ContentFile(buf.getvalue(), name=f"{digest[:16]}.png"),
        width=img.width,
        height=img.height,
        source_format=fmt,
        sha256=digest,
        metadata=metadata,
    )


def _looks_like_dicom(data: bytes) -> bool:
    return len(data) > 132 and data[128:132] == b"DICM"


def _read_raster(data: bytes):
    try:
        img = PILImage.open(io.BytesIO(data))
        if img.width * img.height > MAX_PIXELS:
            raise UploadRejected("Image is too large")
        img.load()
    except UploadRejected:
        raise
    except Exception as e:  # Pillow raises a variety of types for bad input
        raise UploadRejected(f"Not a readable image: {e}") from e

    fmt = (img.format or "").lower()
    fmt = {"jpg": "jpeg", "mpo": "jpeg"}.get(fmt, fmt)
    if fmt not in {"png", "jpeg", "tiff"}:
        raise UploadRejected(f"Unsupported format {img.format}; use DICOM, PNG, JPEG or TIFF")
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB") if img.mode in ("RGBA", "P", "CMYK") else img.convert("L")
    return img, fmt


def _read_dicom(data: bytes):
    try:
        ds = pydicom.dcmread(io.BytesIO(data))
    except InvalidDicomError as e:
        raise UploadRejected(f"Not a valid DICOM file: {e}") from e

    if str(ds.get("BurnedInAnnotation", "")).upper() == "YES":
        # Identifying text drawn into the pixels cannot be removed by header filtering.
        raise UploadRejected("DICOM declares burned-in annotation; de-identify the pixels before upload")
    if "PixelData" not in ds:
        raise UploadRejected("DICOM file has no pixel data")

    rows, cols = int(ds.get("Rows", 0)), int(ds.get("Columns", 0))
    if rows * cols > MAX_PIXELS:
        raise UploadRejected("Image is too large")

    try:
        pixels = ds.pixel_array
    except Exception as e:
        raise UploadRejected(f"Cannot decode pixel data (compressed transfer syntax?): {e}") from e

    if int(ds.get("NumberOfFrames", 1)) > 1:
        pixels = pixels[len(pixels) // 2]  # middle frame as the representative slice

    photometric = str(ds.get("PhotometricInterpretation", "MONOCHROME2"))
    if photometric == "RGB":
        array = pixels.astype(np.uint8)
    else:
        array = _window_to_uint8(ds, pixels, invert=photometric == "MONOCHROME1")

    return array, _whitelisted_metadata(ds)


def _window_to_uint8(ds, pixels: np.ndarray, invert: bool) -> np.ndarray:
    values = pixels.astype(np.float64)
    slope = float(ds.get("RescaleSlope", 1) or 1)
    intercept = float(ds.get("RescaleIntercept", 0) or 0)
    values = values * slope + intercept

    center, width = _first(ds.get("WindowCenter")), _first(ds.get("WindowWidth"))
    if center is not None and width is not None and width > 0:
        low, high = center - width / 2.0, center + width / 2.0
    else:
        low, high = float(values.min()), float(values.max())
    if high <= low:
        high = low + 1.0

    scaled = np.clip((values - low) / (high - low), 0.0, 1.0) * 255.0
    out = scaled.round().astype(np.uint8)
    return 255 - out if invert else out


def _first(value):
    if value is None:
        return None
    if isinstance(value, pydicom.multival.MultiValue):
        value = value[0] if len(value) else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _whitelisted_metadata(ds) -> dict:
    out = {}
    for keyword in KEEP_DICOM:
        if keyword in ds:
            value = ds.get(keyword)
            if isinstance(value, pydicom.multival.MultiValue):
                value = [_jsonable(v) for v in value]
            else:
                value = _jsonable(value)
            out[keyword] = value
    return out


def _jsonable(v):
    if isinstance(v, (int, float, str)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)
