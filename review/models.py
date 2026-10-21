"""
Domain model.

A Study holds Images. Each annotator works on an image through their own AnnotationTask,
which moves draft -> submitted -> approved | rejected. A reviewer who is not the
annotator decides. Every state change writes an AuditEvent into a hash chain.
"""

import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Study(models.Model):
    class Modality(models.TextChoices):
        CT = "CT", "Computed tomography"
        MR = "MR", "Magnetic resonance"
        CR = "CR", "Radiography"
        US = "US", "Ultrasound"
        SM = "SM", "Slide microscopy"
        OT = "OT", "Other"

    title = models.CharField(max_length=200)
    modality = models.CharField(max_length=2, choices=Modality.choices, default=Modality.OT)
    # Pseudonymous reference only. Names and dates of birth never enter this system.
    subject_ref = models.CharField(max_length=64, help_text="Pseudonymous subject or case reference")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "studies"

    def __str__(self):
        return f"{self.title} ({self.subject_ref})"


class Image(models.Model):
    class SourceFormat(models.TextChoices):
        DICOM = "dicom", "DICOM"
        PNG = "png", "PNG"
        JPEG = "jpeg", "JPEG"
        TIFF = "tiff", "TIFF"

    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="images")
    # Always a de-identified 8-bit PNG rendering; the original upload is not kept.
    file = models.ImageField(upload_to="images/%Y/%m/")
    original_name = models.CharField(max_length=255)
    source_format = models.CharField(max_length=10, choices=SourceFormat.choices)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64, help_text="Hash of the uploaded bytes, for duplicate detection")
    # Whitelisted technical DICOM attributes only. See services/ingest.py.
    metadata = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["study", "sha256"], name="uniq_image_per_study"),
        ]

    def __str__(self):
        return f"Image {self.pk} of study {self.study_id}"


class Label(models.Model):
    name = models.CharField(max_length=80, unique=True)
    color = models.CharField(max_length=7, default="#53057F", help_text="Hex colour used in the viewer")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AnnotationTask(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    EDITABLE = {Status.DRAFT, Status.REJECTED}

    image = models.ForeignKey(Image, on_delete=models.CASCADE, related_name="tasks")
    annotator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasks")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["image", "annotator"], name="one_task_per_reader_per_image"),
        ]

    def __str__(self):
        return f"Task {self.pk} ({self.annotator} on image {self.image_id}, {self.status})"

    @property
    def is_editable(self) -> bool:
        return self.status in self.EDITABLE


class Annotation(models.Model):
    class Shape(models.TextChoices):
        BOX = "box", "Bounding box"
        POLYGON = "polygon", "Polygon"

    task = models.ForeignKey(AnnotationTask, on_delete=models.CASCADE, related_name="annotations")
    label = models.ForeignKey(Label, on_delete=models.PROTECT)
    shape = models.CharField(max_length=10, choices=Shape.choices)
    # box: [x, y, width, height]; polygon: [[x1, y1], [x2, y2], ...] in image pixel coordinates
    geometry = models.JSONField()
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.label} {self.shape} on task {self.task_id}"

    def clean(self):
        from review.services.geometry import validate_geometry

        try:
            validate_geometry(self.shape, self.geometry, self.task.image.width, self.task.image.height)
        except ValueError as e:
            raise ValidationError({"geometry": str(e)}) from e


class Review(models.Model):
    class Decision(models.TextChoices):
        APPROVE = "approve", "Approve"
        REJECT = "reject", "Reject"

    task = models.ForeignKey(AnnotationTask, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    decision = models.CharField(max_length=10, choices=Decision.choices)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.decision} of task {self.task_id} by {self.reviewer}"


class AuditEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("Audit events are append-only")

    def delete(self):
        raise PermissionError("Audit events are append-only")


class AuditEvent(models.Model):
    """
    Append-only, hash-chained log. Each event's hash covers its content and the previous
    event's hash, so editing or deleting a row in the database breaks verification for
    every event after it. This makes tampering evident; it does not prevent a database
    administrator from rewriting the whole chain. That needs an external anchor.
    """

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=40)
    object_type = models.CharField(max_length=40)
    object_id = models.CharField(max_length=40)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField()
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)

    objects = AuditEventQuerySet.as_manager()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"#{self.pk} {self.action} {self.object_type}:{self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("Audit events are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit events are append-only")

    def canonical(self) -> str:
        return json.dumps(
            {
                "actor": self.actor_id,
                "action": self.action,
                "object_type": self.object_type,
                "object_id": str(self.object_id),
                "payload": self.payload,
                "created_at": self.created_at.isoformat(),
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def compute_hash(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()
