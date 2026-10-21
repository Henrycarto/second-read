"""
Demo data: three users, three labels, one study of synthetic images, and two independent
readings of the first image already submitted, so the review and agreement screens have
something to show. All data is generated; there is no patient data in this repository.
"""

import io
import os

import numpy as np
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFilter

from review.models import Image, Label, Study
from review.services import roles, workflow
from review.services.ingest import process_upload

LABELS = [("tumour region", "#53057F"), ("lymphocyte cluster", "#0A7E8C"), ("artefact", "#C77700")]


def synthetic_slide(seed: int, size=(768, 512)) -> bytes:
    """A stained-tissue look-alike: textured background with a few dark blobs."""
    rng = np.random.default_rng(seed)
    w, h = size
    base = np.stack([
        np.full((h, w), 232) - rng.normal(0, 8, (h, w)),
        np.full((h, w), 196) - rng.normal(0, 8, (h, w)),
        np.full((h, w), 222) - rng.normal(0, 8, (h, w)),
    ], axis=-1).clip(0, 255).astype(np.uint8)
    img = PILImage.fromarray(base, "RGB")
    draw = ImageDraw.Draw(img)
    for _ in range(40):
        x, y, r = rng.integers(0, w), rng.integers(0, h), rng.integers(3, 9)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(120, 60, 150))
    draw.ellipse([200, 140, 420, 330], fill=(150, 80, 170))
    draw.ellipse([520, 300, 600, 380], fill=(90, 40, 130))
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class Command(BaseCommand):
    help = "Create demo users, labels and a synthetic study"

    def handle(self, *args, **options):
        password = os.environ.get("DEMO_PASSWORD", "second-read-demo")
        with transaction.atomic():
            annotator_group, _ = Group.objects.get_or_create(name=roles.ANNOTATOR)
            reviewer_group, _ = Group.objects.get_or_create(name=roles.REVIEWER)

            users = {}
            for name, group in [("anna", annotator_group), ("ben", annotator_group), ("rita", reviewer_group)]:
                user, created = User.objects.get_or_create(username=name)
                if created:
                    user.set_password(password)
                    user.save()
                user.groups.add(group)
                users[name] = user

            labels = {}
            for name, color in LABELS:
                labels[name], _ = Label.objects.get_or_create(name=name, defaults={"color": color})

            if Study.objects.filter(subject_ref="DEMO-0001").exists():
                self.stdout.write("Demo study already exists; users and labels ensured.")
                return

            study = Study.objects.create(title="Synthetic H&E tiles", modality=Study.Modality.SM,
                                         subject_ref="DEMO-0001", created_by=users["rita"])
            images = []
            for seed in (1, 2, 3):
                p = process_upload(synthetic_slide(seed), f"tile_{seed}.png")
                images.append(Image.objects.create(
                    study=study, file=p.png, original_name=f"tile_{seed}.png", source_format=p.source_format,
                    width=p.width, height=p.height, sha256=p.sha256, metadata=p.metadata,
                    uploaded_by=users["rita"]))

            # Two independent readings of image 1 that mostly agree, with one real difference.
            first = images[0]
            readings = {
                "anna": [("tumour region", "polygon",
                          [[205, 230], [250, 150], [330, 138], [410, 190], [415, 280], [330, 335], [240, 315]]),
                         ("lymphocyte cluster", "box", [518, 298, 84, 84])],
                "ben": [("tumour region", "polygon",
                         [[200, 235], [240, 160], [320, 140], [405, 185], [420, 275], [340, 330], [230, 320]]),
                        ("lymphocyte cluster", "box", [522, 304, 76, 74]),
                        ("artefact", "box", [20, 20, 60, 40])],
            }
            for reader, shapes in readings.items():
                task = workflow.start_task(first, users[reader])
                for label, shape, geometry in shapes:
                    workflow.add_annotation(task, users[reader], label=labels[label], shape=shape, geometry=geometry)
                workflow.submit(task, users[reader])

        self.stdout.write(self.style.SUCCESS(
            f"Demo ready. Users anna, ben (annotators) and rita (reviewer), password {password!r}. Dev use only."))
