from django.contrib import admin

from review.models import Annotation, AnnotationTask, AuditEvent, Image, Label, Review, Study


@admin.register(Study)
class StudyAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "modality", "subject_ref", "created_by", "created_at")
    search_fields = ("title", "subject_ref")


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ("id", "study", "original_name", "source_format", "width", "height", "uploaded_at")
    readonly_fields = ("sha256", "metadata")


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    list_display = ("name", "color")


class AnnotationInline(admin.TabularInline):
    model = Annotation
    extra = 0
    readonly_fields = ("label", "shape", "geometry", "note")
    can_delete = False


@admin.register(AnnotationTask)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("id", "image", "annotator", "status", "submitted_at", "decided_at")
    list_filter = ("status",)
    inlines = [AnnotationInline]
    # State changes belong to the workflow, which writes the audit trail.
    readonly_fields = ("image", "annotator", "status", "submitted_at", "decided_at")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("id", "task", "reviewer", "decision", "created_at")
    readonly_fields = ("task", "reviewer", "decision", "comment")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "actor", "action", "object_type", "object_id")
    list_filter = ("action", "object_type")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
