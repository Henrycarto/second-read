"""
REST API. Views stay thin: permissions and HTTP mapping here, rules in services/.

Visibility is part of the design, not an afterthought: an annotator only ever sees their
own task on an image, so the second reading is independent of the first. Reviewers see
everything.
"""

from django.conf import settings
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from review.models import Annotation, AnnotationTask, AuditEvent, Image, Label, Study
from review.serializers import (
    AnnotationSerializer,
    AuditEventSerializer,
    DecisionSerializer,
    ImageSerializer,
    ImageUploadSerializer,
    LabelSerializer,
    StudySerializer,
    TaskSerializer,
)
from review.services import agreement, audit, roles, workflow
from review.services.export import coco_for_study
from review.services.ingest import UploadRejected, process_upload


class IsReviewer(BasePermission):
    def has_permission(self, request, view):
        return roles.is_reviewer(request.user)


class HasAnyRole(BasePermission):
    def has_permission(self, request, view):
        return roles.is_annotator(request.user) or roles.is_reviewer(request.user)


def run(fn, *args, **kwargs):
    """Map rule violations to HTTP: forbidden -> 403, invalid geometry -> 400, wrong state -> 409."""
    try:
        return fn(*args, **kwargs)
    except workflow.WorkflowError as e:
        if e.forbidden:
            raise PermissionDenied(str(e)) from e
        raise Conflict(str(e)) from e
    except ValueError as e:
        raise ValidationError({"geometry": str(e)}) from e


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Conflicts with the current state"


def visible_tasks(user):
    qs = AnnotationTask.objects.select_related("image", "annotator").prefetch_related(
        "annotations__label", "reviews__reviewer")
    if roles.is_reviewer(user):
        return qs
    return qs.filter(annotator=user)


class StudyViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                   viewsets.GenericViewSet):
    queryset = Study.objects.select_related("created_by")
    serializer_class = StudySerializer
    permission_classes = [HasAnyRole]

    def perform_create(self, serializer):
        with transaction.atomic():
            study = serializer.save(created_by=self.request.user)
            audit.record(self.request.user, "study.created", study,
                         {"title": study.title, "subject_ref": study.subject_ref})

    @action(detail=True, methods=["get", "post"], url_path="images",
            parser_classes=[MultiPartParser, FormParser])
    def images(self, request, pk=None):
        study = self.get_object()
        if request.method == "GET":
            return Response(ImageSerializer(study.images.all(), many=True, context={"request": request}).data)

        upload = ImageUploadSerializer(data=request.data)
        upload.is_valid(raise_exception=True)
        f = upload.validated_data["file"]
        if f.size > settings.SECOND_READ_MAX_UPLOAD_BYTES:
            raise ValidationError({"file": "File is too large"})
        try:
            processed = process_upload(f.read(), f.name)
        except UploadRejected as e:
            raise ValidationError({"file": str(e)}) from e

        try:
            with transaction.atomic():
                image = Image.objects.create(
                    study=study, file=processed.png, original_name=f.name[:255],
                    source_format=processed.source_format, width=processed.width, height=processed.height,
                    sha256=processed.sha256, metadata=processed.metadata, uploaded_by=request.user)
                audit.record(request.user, "image.uploaded", image,
                             {"study": study.pk, "sha256": processed.sha256, "format": processed.source_format})
        except IntegrityError:
            raise Conflict("This file is already in the study") from None
        return Response(ImageSerializer(image, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="export/coco", permission_classes=[IsReviewer])
    def export_coco(self, request, pk=None):
        return Response(coco_for_study(self.get_object()))


class ImageViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Image.objects.select_related("study", "uploaded_by")
    serializer_class = ImageSerializer
    permission_classes = [HasAnyRole]

    @action(detail=True, methods=["get", "post"])
    def tasks(self, request, pk=None):
        image = self.get_object()
        if request.method == "POST":
            task = run(workflow.start_task, image, request.user)
            return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)
        tasks = visible_tasks(request.user).filter(image=image)
        return Response(TaskSerializer(tasks, many=True).data)

    @action(detail=True, methods=["get"], permission_classes=[IsReviewer])
    def agreement(self, request, pk=None):
        image = self.get_object()
        try:
            a_id, b_id = int(request.query_params["task_a"]), int(request.query_params["task_b"])
            threshold = float(request.query_params.get("iou_threshold", 0.5))
        except (KeyError, ValueError):
            raise ValidationError("task_a and task_b (task ids) are required") from None
        if not 0 < threshold <= 1:
            raise ValidationError("iou_threshold must be in (0, 1]")

        task_a = get_object_or_404(AnnotationTask, pk=a_id, image=image)
        task_b = get_object_or_404(AnnotationTask, pk=b_id, image=image)
        if task_a.pk == task_b.pk:
            raise ValidationError("Pick two different readings")
        if AnnotationTask.Status.DRAFT in (task_a.status, task_b.status):
            raise Conflict("Both readings must be submitted before they are compared")

        def rows(task):
            return [(a.label.name, a.shape, a.geometry) for a in task.annotations.select_related("label")]

        return Response({
            "image": image.pk,
            "task_a": {"id": task_a.pk, "annotator": task_a.annotator.get_username()},
            "task_b": {"id": task_b.pk, "annotator": task_b.annotator.get_username()},
            "iou_threshold": threshold,
            "labels": agreement.compare(rows(task_a), rows(task_b), image.width, image.height, threshold),
        })


class TaskViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TaskSerializer
    permission_classes = [HasAnyRole]

    def get_queryset(self):
        qs = visible_tasks(self.request.user)
        status_filter = self.request.query_params.get("status")
        return qs.filter(status=status_filter) if status_filter else qs

    @action(detail=True, methods=["get", "post"])
    def annotations(self, request, pk=None):
        task = self.get_object()
        if request.method == "GET":
            return Response(AnnotationSerializer(task.annotations.select_related("label"), many=True).data)
        ser = AnnotationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ann = run(workflow.add_annotation, task, request.user, **ser.validated_data)
        return Response(AnnotationSerializer(ann).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        task = run(workflow.submit, self.get_object(), request.user)
        return Response(TaskSerializer(task).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._decide(request, "approve")

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._decide(request, "reject")

    def _decide(self, request, decision):
        ser = DecisionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        task = self.get_object()
        run(workflow.decide, task, request.user, decision, ser.validated_data["comment"])
        task.refresh_from_db()
        return Response(TaskSerializer(task).data)


class AnnotationViewSet(mixins.RetrieveModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin,
                        viewsets.GenericViewSet):
    serializer_class = AnnotationSerializer
    permission_classes = [HasAnyRole]

    def get_queryset(self):
        qs = Annotation.objects.select_related("label", "task__image")
        if roles.is_reviewer(self.request.user):
            return qs
        return qs.filter(task__annotator=self.request.user)

    def perform_update(self, serializer):
        changes = dict(serializer.validated_data)
        run(workflow.update_annotation, serializer.instance, self.request.user, **changes)

    def perform_destroy(self, instance):
        run(workflow.delete_annotation, instance, self.request.user)


class LabelViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Label.objects.all()
    serializer_class = LabelSerializer
    pagination_class = None


class AuditLogView(APIView):
    permission_classes = [IsReviewer]

    def get(self, request):
        events = AuditEvent.objects.select_related("actor").order_by("-id")
        object_type, object_id = request.query_params.get("object_type"), request.query_params.get("object_id")
        if object_type:
            events = events.filter(object_type=object_type)
        if object_id:
            events = events.filter(object_id=object_id)
        return Response(AuditEventSerializer(events[:200], many=True).data)


class AuditVerifyView(APIView):
    permission_classes = [IsReviewer]

    def get(self, request):
        report = audit.verify_chain()
        return Response({"ok": report.ok, "checked": report.checked,
                         "first_bad_id": report.first_bad_id, "reason": report.reason})
