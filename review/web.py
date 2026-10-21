"""Server-rendered pages. All data changes go through the REST API from static/review/app.js."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from review.api import visible_tasks
from review.models import AnnotationTask, Image, Label, Study
from review.services import roles


def _ctx(request, **extra):
    return {
        "is_annotator": roles.is_annotator(request.user),
        "is_reviewer": roles.is_reviewer(request.user),
        **extra,
    }


@login_required
def study_list(request):
    return render(request, "review/study_list.html", _ctx(request, studies=Study.objects.all()))


@login_required
def study_detail(request, pk):
    study = get_object_or_404(Study, pk=pk)
    return render(request, "review/study_detail.html", _ctx(request, study=study, images=study.images.all()))


@login_required
def annotate(request, pk):
    if not roles.is_annotator(request.user):
        raise PermissionDenied
    image = get_object_or_404(Image, pk=pk)
    return render(request, "review/viewer.html", _ctx(
        request, image=image, labels=Label.objects.all(), mode="annotate", task=None))


@login_required
def review_queue(request):
    if not roles.is_reviewer(request.user):
        raise PermissionDenied
    tasks = (visible_tasks(request.user)
             .filter(status=AnnotationTask.Status.SUBMITTED)
             .exclude(annotator=request.user)
             .order_by("submitted_at"))
    return render(request, "review/queue.html", _ctx(request, tasks=tasks))


@login_required
def review_task(request, pk):
    if not roles.is_reviewer(request.user):
        raise PermissionDenied
    task = get_object_or_404(AnnotationTask.objects.select_related("image", "annotator"), pk=pk)
    others = (AnnotationTask.objects.filter(image=task.image).exclude(pk=task.pk)
              .exclude(status=AnnotationTask.Status.DRAFT).select_related("annotator"))
    return render(request, "review/viewer.html", _ctx(
        request, image=task.image, labels=Label.objects.all(), mode="review", task=task, others=others))
