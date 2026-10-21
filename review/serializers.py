from rest_framework import serializers

from review.models import Annotation, AnnotationTask, AuditEvent, Image, Label, Review, Study


class StudySerializer(serializers.ModelSerializer):
    created_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    image_count = serializers.IntegerField(source="images.count", read_only=True)

    class Meta:
        model = Study
        fields = ["id", "title", "modality", "subject_ref", "created_by", "created_at", "image_count"]


class ImageSerializer(serializers.ModelSerializer):
    uploaded_by = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = Image
        fields = ["id", "study", "file", "original_name", "source_format", "width", "height",
                  "sha256", "metadata", "uploaded_by", "uploaded_at"]
        read_only_fields = fields


class ImageUploadSerializer(serializers.Serializer):
    file = serializers.FileField()


class LabelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Label
        fields = ["id", "name", "color"]


class AnnotationSerializer(serializers.ModelSerializer):
    label = serializers.SlugRelatedField(slug_field="name", queryset=Label.objects.all())

    class Meta:
        model = Annotation
        fields = ["id", "task", "label", "shape", "geometry", "note", "created_at", "updated_at"]
        read_only_fields = ["id", "task", "created_at", "updated_at"]


class ReviewSerializer(serializers.ModelSerializer):
    reviewer = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = Review
        fields = ["id", "reviewer", "decision", "comment", "created_at"]


class TaskSerializer(serializers.ModelSerializer):
    annotator = serializers.SlugRelatedField(slug_field="username", read_only=True)
    annotations = AnnotationSerializer(many=True, read_only=True)
    reviews = ReviewSerializer(many=True, read_only=True)

    class Meta:
        model = AnnotationTask
        fields = ["id", "image", "annotator", "status", "created_at", "submitted_at", "decided_at",
                  "annotations", "reviews"]
        read_only_fields = fields


class DecisionSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class AuditEventSerializer(serializers.ModelSerializer):
    actor = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = AuditEvent
        fields = ["id", "actor", "action", "object_type", "object_id", "payload", "created_at",
                  "prev_hash", "hash"]
