import pytest

from review.services.geometry import bbox, polygon_area, rasterise, validate_geometry


def test_valid_box_and_polygon():
    validate_geometry("box", [0, 0, 10, 10], 100, 100)
    validate_geometry("polygon", [[0, 0], [10, 0], [10, 10]], 100, 100)


@pytest.mark.parametrize("shape,geometry,msg", [
    ("box", [0, 0, 0, 10], "positive"),
    ("box", [95, 0, 10, 10], "outside"),
    ("box", [0, 0, 10], "x, y, width, height"),
    ("box", [0, 0, True, 10], "x, y, width, height"),
    ("polygon", [[0, 0], [10, 0]], "between 3"),
    ("polygon", [[0, 0], [5, 5], [10, 10]], "zero area"),
    ("polygon", [[0, 0], [500, 0], [0, 10]], "outside"),
    ("circle", [1, 2, 3], "unknown shape"),
])
def test_invalid_geometry(shape, geometry, msg):
    with pytest.raises(ValueError, match=msg):
        validate_geometry(shape, geometry, 100, 100)


def test_area_and_bbox():
    tri = [[0, 0], [10, 0], [0, 10]]
    assert polygon_area(tri) == 50
    assert bbox("polygon", tri) == [0, 0, 10, 10]
    assert bbox("box", [1, 2, 3, 4]) == [1, 2, 3, 4]


def test_rasterised_box_covers_exactly_its_pixels():
    mask = rasterise([("box", [10, 20, 30, 40])], 100, 100)
    assert mask.sum() == 30 * 40
    assert mask[20, 10] and not mask[19, 10] and not mask[20, 40]
