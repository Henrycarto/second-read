"""
The review state machine. All transitions go through here, never through model.save()
in views, so the rules and the audit trail live in one place.

    draft ──submit──> submitted ──approve──> approved
      ^                   │
      └──── rejected <────┘ reject (comment required)
            (editable, can be resubmitted)
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from review.models import Annotation, AnnotationTask, Review
from review.services import audit, roles
from review.services.geometry import validate_geometry

Status = AnnotationTask.Status


class WorkflowError(Exception):
    """A request that breaks the review rules. Maps to HTTP 409 or 403."""

    def __init__(self, message: str, forbidden: bool = False):
        super().__init__(message)
        self.forbidden = forbidden


def start_task(image, user) -> AnnotationTask:
    if not roles.is_annotator(user):
        raise WorkflowError("Only annotators can start a task", forbidden=True)
    with transaction.atomic():
        task, created = AnnotationTask.objects.get_or_create(image=image, annotator=user)
        if created:
            audit.record(user, "task.created", task, {"image": image.pk})
    return task


def _require_owner_and_editable(task: AnnotationTask, user) -> None:
    if task.annotator_id != user.pk:
        raise WorkflowError("Only the task's annotator can change it", forbidden=True)
    if not task.is_editable:
        raise WorkflowError(f"Task is {task.status} and cannot be edited")


def add_annotation(task, user, *, label, shape, geometry, note="") -> Annotation:
    with transaction.atomic():
        task = AnnotationTask.objects.select_for_update().get(pk=task.pk)
        _require_owner_and_editable(task, user)
        validate_geometry(shape, geometry, task.image.width, task.image.height)
        ann = Annotation.objects.create(task=task, label=label, shape=shape, geometry=geometry, note=note)
        audit.record(user, "annotation.created", ann,
                     {"task": task.pk, "label": label.name, "shape": shape, "geometry": geometry})
        return ann


def update_annotation(ann: Annotation, user, **changes) -> Annotation:
    with transaction.atomic():
        task = AnnotationTask.objects.select_for_update().get(pk=ann.task_id)
        _require_owner_and_editable(task, user)
        before = {"label": ann.label.name, "shape": ann.shape, "geometry": ann.geometry, "note": ann.note}
        for field in ("label", "shape", "geometry", "note"):
            if field in changes:
                setattr(ann, field, changes[field])
        validate_geometry(ann.shape, ann.geometry, task.image.width, task.image.height)
        ann.save()
        after = {"label": ann.label.name, "shape": ann.shape, "geometry": ann.geometry, "note": ann.note}
        audit.record(user, "annotation.updated", ann, {"task": task.pk, "before": before, "after": after})
        return ann


def delete_annotation(ann: Annotation, user) -> None:
    with transaction.atomic():
        task = AnnotationTask.objects.select_for_update().get(pk=ann.task_id)
        _require_owner_and_editable(task, user)
        snapshot = {"task": task.pk, "label": ann.label.name, "shape": ann.shape, "geometry": ann.geometry}
        audit.record(user, "annotation.deleted", ann, snapshot)
        ann.delete()


def submit(task: AnnotationTask, user) -> AnnotationTask:
    with transaction.atomic():
        task = AnnotationTask.objects.select_for_update().get(pk=task.pk)
        _require_owner_and_editable(task, user)
        if not task.annotations.exists():
            raise WorkflowError("Cannot submit a task with no annotations")
        task.status = Status.SUBMITTED
        task.submitted_at = timezone.now()
        task.save(update_fields=["status", "submitted_at"])
        audit.record(user, "task.submitted", task, {"annotations": task.annotations.count()})
        return task


def decide(task: AnnotationTask, reviewer, decision: str, comment: str = "") -> Review:
    if not roles.is_reviewer(reviewer):
        raise WorkflowError("Only reviewers can approve or reject", forbidden=True)
    if decision not in Review.Decision.values:
        raise WorkflowError(f"Unknown decision {decision!r}")
    if decision == Review.Decision.REJECT and not comment.strip():
        raise WorkflowError("A rejection needs a comment the annotator can act on")

    with transaction.atomic():
        task = AnnotationTask.objects.select_for_update().get(pk=task.pk)
        if task.annotator_id == reviewer.pk:
            # Four-eyes principle: nobody reviews their own reading, superuser or not.
            raise WorkflowError("Reviewers cannot decide on their own task", forbidden=True)
        if task.status != Status.SUBMITTED:
            raise WorkflowError(f"Task is {task.status}; only submitted tasks can be reviewed")

        review = Review.objects.create(task=task, reviewer=reviewer, decision=decision, comment=comment)
        task.status = Status.APPROVED if decision == Review.Decision.APPROVE else Status.REJECTED
        task.decided_at = timezone.now()
        task.save(update_fields=["status", "decided_at"])
        audit.record(reviewer, f"task.{task.status}", task, {"review": review.pk, "comment": comment})
        return review
