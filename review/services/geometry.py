"""Shape validation and rasterisation. Coordinates are image pixels, origin top-left."""

from __future__ import annotations

import numpy as np
from PIL import Image as PILImage
from PIL import ImageDraw

MAX_POLYGON_POINTS = 2000


def validate_geometry(shape: str, geometry, width: int, height: int) -> None:
    if shape == "box":
        if not (isinstance(geometry, list) and len(geometry) == 4 and all(_is_number(v) for v in geometry)):
            raise ValueError("box geometry must be [x, y, width, height]")
        x, y, w, h = geometry
        if w <= 0 or h <= 0:
            raise ValueError("box width and height must be positive")
        if x < 0 or y < 0 or x + w > width or y + h > height:
            raise ValueError("box lies outside the image")
        return

    if shape == "polygon":
        if not isinstance(geometry, list) or not (3 <= len(geometry) <= MAX_POLYGON_POINTS):
            raise ValueError(f"polygon needs between 3 and {MAX_POLYGON_POINTS} points")
        for point in geometry:
            if not (isinstance(point, list) and len(point) == 2 and all(_is_number(v) for v in point)):
                raise ValueError("polygon points must be [x, y]")
            px, py = point
            if px < 0 or py < 0 or px > width or py > height:
                raise ValueError("polygon point lies outside the image")
        if polygon_area(geometry) <= 0:
            raise ValueError("polygon has zero area")
        return

    raise ValueError(f"unknown shape {shape!r}")


def polygon_area(points) -> float:
    """Shoelace formula."""
    area = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def as_polygon(shape: str, geometry) -> list[list[float]]:
    if shape == "box":
        x, y, w, h = geometry
        return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
    return geometry


def bbox(shape: str, geometry) -> list[float]:
    """COCO-style [x, y, width, height]."""
    if shape == "box":
        return list(geometry)
    xs = [p[0] for p in geometry]
    ys = [p[1] for p in geometry]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def rasterise(shapes, width: int, height: int) -> np.ndarray:
    """
    Boolean mask of the union of shapes. Pixel (i, j) is inside when its centre is.
    Good enough for agreement metrics; sub-pixel exactness is not the point.
    """
    canvas = PILImage.new("1", (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    for shape, geometry in shapes:
        pts = [(float(x), float(y)) for x, y in as_polygon(shape, geometry)]
        if shape == "box":
            x, y, w, h = geometry
            # Box covers pixels whose centres fall in [x, x+w) x [y, y+h)
            draw.rectangle([x, y, x + w - 1, y + h - 1], fill=1)
        else:
            draw.polygon(pts, fill=1)
    return np.array(canvas, dtype=bool)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)
