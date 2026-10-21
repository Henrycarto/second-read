"""COCO export of approved readings only: unreviewed work never leaks into a training set."""

from __future__ import annotations

from django.utils import timezone

from review.models import AnnotationTask, Label
from review.services.geometry import as_polygon, bbox, polygon_area


def coco_for_study(study) -> dict:
    labels = list(Label.objects.order_by("id"))
    category_ids = {label.pk: i + 1 for i, label in enumerate(labels)}

    images, annotations = [], []
    tasks = (AnnotationTask.objects
             .filter(image__study=study, status=AnnotationTask.Status.APPROVED)
             .select_related("image", "annotator")
             .prefetch_related("annotations__label")
             .order_by("image_id", "id"))

    seen_images = set()
    ann_id = 1
    for task in tasks:
        image = task.image
        if image.pk not in seen_images:
            seen_images.add(image.pk)
            images.append({
                "id": image.pk,
                "file_name": image.file.name,
                "width": image.width,
                "height": image.height,
            })
        for ann in task.annotations.all():
            polygon = as_polygon(ann.shape, ann.geometry)
            annotations.append({
                "id": ann_id,
                "image_id": image.pk,
                "category_id": category_ids[ann.label_id],
                "bbox": bbox(ann.shape, ann.geometry),
                "segmentation": [[c for point in polygon for c in point]],
                "area": polygon_area(polygon),
                "iscrowd": 0,
                # Provenance: which reader, which approved task
                "attributes": {"task_id": task.pk, "annotator": task.annotator.get_username()},
            })
            ann_id += 1

    return {
        "info": {
            "description": f"Approved annotations for study {study.pk}",
            "date_created": timezone.now().isoformat(),
        },
        "images": images,
        "annotations": annotations,
        "categories": [{"id": category_ids[lab.pk], "name": lab.name} for lab in labels],
    }
