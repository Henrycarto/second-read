import pytest

from review.services.agreement import compare


def by_label(rows):
    return {r["label"]: r for r in rows}


def test_identical_readings_agree_perfectly():
    a = [("tumour", "box", [10, 10, 20, 20])]
    r = by_label(compare(a, a, 100, 100))["tumour"]
    assert r["iou"] == 1.0 and r["dice"] == 1.0 and r["f1"] == 1.0


def test_half_overlap_has_known_iou_and_dice():
    a = [("tumour", "box", [0, 0, 20, 10])]
    b = [("tumour", "box", [10, 0, 20, 10])]
    r = by_label(compare(a, b, 100, 100))["tumour"]
    assert r["iou"] == pytest.approx(1 / 3, abs=1e-4)   # 100 / 300
    assert r["dice"] == pytest.approx(0.5, abs=1e-4)    # 2*100 / 400
    assert r["matched"] == 0                            # below the 0.5 IoU instance threshold
    assert r["f1"] == 0.0


def test_label_used_by_only_one_reader_is_total_disagreement():
    a = [("tumour", "box", [0, 0, 10, 10])]
    b = [("tumour", "box", [0, 0, 10, 10]), ("artefact", "box", [50, 50, 10, 10])]
    rows = by_label(compare(a, b, 100, 100))
    assert rows["artefact"]["reader_a_count"] == 0
    assert rows["artefact"]["iou"] == 0.0
    assert rows["artefact"]["f1"] == 0.0
    assert rows["artefact"]["recall"] is None


def test_pixel_overlap_can_hide_a_count_disagreement():
    # A draws one big region, B splits it in three: pixels agree, objects do not.
    a = [("tumour", "box", [0, 0, 60, 20])]
    b = [("tumour", "box", [0, 0, 20, 20]), ("tumour", "box", [20, 0, 20, 20]), ("tumour", "box", [40, 0, 20, 20])]
    r = by_label(compare(a, b, 100, 100))["tumour"]
    assert r["iou"] == 1.0
    assert r["matched"] == 0
    assert r["f1"] == 0.0


def test_matching_is_one_to_one():
    a = [("tumour", "box", [0, 0, 10, 10])]
    b = [("tumour", "box", [0, 0, 10, 10]), ("tumour", "box", [1, 0, 10, 10])]
    r = by_label(compare(a, b, 100, 100))["tumour"]
    assert r["matched"] == 1
    assert r["precision"] == 0.5 and r["recall"] == 1.0
