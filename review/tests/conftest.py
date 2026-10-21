import io

import numpy as np
import pytest
from django.contrib.auth.models import Group, User
from PIL import Image as PILImage
from rest_framework.test import APIClient

from review.models import Image, Label, Study
from review.services import roles
from review.services.ingest import process_upload


@pytest.fixture(autouse=True)
def media_tmp(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


def png_bytes(w=200, h=100, seed=0) -> bytes:
    arr = np.random.default_rng(seed).integers(0, 255, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO()
    PILImage.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def make_user(name, *groups):
    user = User.objects.create_user(name, password="pw-not-used-1")
    for g in groups:
        user.groups.add(Group.objects.get_or_create(name=g)[0])
    return user


@pytest.fixture
def anna(db):
    return make_user("anna", roles.ANNOTATOR)


@pytest.fixture
def ben(db):
    return make_user("ben", roles.ANNOTATOR)


@pytest.fixture
def rita(db):
    return make_user("rita", roles.REVIEWER)


@pytest.fixture
def outsider(db):
    return make_user("outsider")


@pytest.fixture
def labels(db):
    return {n: Label.objects.create(name=n) for n in ("tumour", "artefact")}


@pytest.fixture
def study(rita):
    return Study.objects.create(title="Test study", modality="SM", subject_ref="T-1", created_by=rita)


@pytest.fixture
def image(study, rita):
    p = process_upload(png_bytes(), "tile.png")
    return Image.objects.create(study=study, file=p.png, original_name="tile.png", source_format=p.source_format,
                                width=p.width, height=p.height, sha256=p.sha256, metadata=p.metadata,
                                uploaded_by=rita)


@pytest.fixture
def client_for():
    def make(user):
        c = APIClient()
        c.force_authenticate(user)
        return c
    return make
