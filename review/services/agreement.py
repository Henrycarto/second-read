"""
Inter-reader agreement between two annotators on the same image.

Two views, because they answer different questions:

- Pixel agreement per label (IoU, Dice): do the readers outline the same tissue?
- Instance agreement per label (precision/recall/F1 after one-to-one matching at an
  IoU threshold): do they find the same objects? Two readers can have high pixel
  overlap and still disagree on how many lesions there are.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

import numpy as np

from review.services.geometry import rasterise


@dataclass
class LabelAgreement:
    label: str
    reader_a_count: int
    reader_b_count: int
    iou: float | None
    dice: float | None
    matched: int
    precision: float | None
    recall: float | None
    f1: float | None


def mask_iou(a: np.ndarray, b: np.ndarray) -> float | None:
    union = np.logical_or(a, b).sum()
    if union == 0:
        return None  # neither reader marked anything: agreement is undefined, not perfect
    return float(np.logical_and(a, b).sum() / union)


def mask_dice(a: np.ndarray, b: np.ndarray) -> float | None:
    total = a.sum() + b.sum()
    if total == 0:
        return None
    return float(2 * np.logical_and(a, b).sum() / total)


def match_instances(masks_a, masks_b, threshold: float):
    """
    Greedy one-to-one matching on IoU, highest pairs first. Returns matched pairs.
    Greedy is not optimal in pathological cases, but for annotation review
    (few objects per label per image) it agrees with Hungarian matching in practice
    and has no extra dependency.
    """
    pairs = []
    for i, ma in enumerate(masks_a):
        for j, mb in enumerate(masks_b):
            iou = mask_iou(ma, mb)
            if iou is not None and iou >= threshold:
                pairs.append((iou, i, j))
    pairs.sort(reverse=True)
    used_a, used_b, matched = set(), set(), []
    for iou, i, j in pairs:
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            matched.append((i, j, iou))
    return matched


def compare(annotations_a, annotations_b, width: int, height: int, iou_threshold: float = 0.5):
    """annotations_* are iterables of (label_name, shape, geometry)."""
    by_label_a, by_label_b = defaultdict(list), defaultdict(list)
    for label, shape, geometry in annotations_a:
        by_label_a[label].append((shape, geometry))
    for label, shape, geometry in annotations_b:
        by_label_b[label].append((shape, geometry))

    results = []
    for label in sorted(set(by_label_a) | set(by_label_b)):
        shapes_a, shapes_b = by_label_a[label], by_label_b[label]
        union_a = rasterise(shapes_a, width, height)
        union_b = rasterise(shapes_b, width, height)

        inst_a = [rasterise([s], width, height) for s in shapes_a]
        inst_b = [rasterise([s], width, height) for s in shapes_b]
        matched = match_instances(inst_a, inst_b, iou_threshold)
        m = len(matched)
        precision = m / len(shapes_b) if shapes_b else None  # B measured against A as reference
        recall = m / len(shapes_a) if shapes_a else None
        # The label appears for at least one reader, so F1 is defined: a label only one
        # reader used is total disagreement (0), not missing data.
        p, r = precision or 0.0, recall or 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0

        results.append(LabelAgreement(
            label=label,
            reader_a_count=len(shapes_a),
            reader_b_count=len(shapes_b),
            iou=_round(mask_iou(union_a, union_b)),
            dice=_round(mask_dice(union_a, union_b)),
            matched=m,
            precision=_round(precision),
            recall=_round(recall),
            f1=_round(f1),
        ))
    return [asdict(r) for r in results]


def _round(v):
    return None if v is None else round(v, 4)
