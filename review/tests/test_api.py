import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from review.models import AnnotationTask

from .conftest import png_bytes
from .test_ingest import dicom_bytes

BOX = {"label": "tumour", "shape": "box", "geometry": [10, 10, 20, 20]}


def reading(client, image, ann=BOX, submit=True):
    task = client.post(f"/api/images/{image.pk}/tasks/").json()
    assert client.post(f"/api/tasks/{task['id']}/annotations/", ann, format="json").status_code == 201
    if submit:
        assert client.post(f"/api/tasks/{task['id']}/submit/").status_code == 200
    return task["id"]


def test_requires_login_and_a_role(client, outsider, client_for, image):
    assert client.get("/api/studies/").status_code in (401, 403)
    assert client_for(outsider).get("/api/studies/").status_code == 403


def test_api_route_names_do_not_resolve_to_web_pages():
    assert reverse("study-list") == "/api/studies/"
    assert reverse("study-detail", args=[1]) == "/api/studies/1/"


def test_upload_png_and_dicom_and_reject_duplicates(client_for, anna, study):
    c = client_for(anna)
    r = c.post(f"/api/studies/{study.pk}/images/", {"file": SimpleUploadedFile("a.png", png_bytes())})
    assert r.status_code == 201, r.content
    assert r.json()["source_format"] == "png"

    r = c.post(f"/api/studies/{study.pk}/images/", {"file": SimpleUploadedFile("b.dcm", dicom_bytes())})
    assert r.status_code == 201, r.content
    assert "PatientName" not in r.json()["metadata"]

    r = c.post(f"/api/studies/{study.pk}/images/", {"file": SimpleUploadedFile("a-again.png", png_bytes())})
    assert r.status_code == 409


def test_upload_garbage_is_400(client_for, anna, study):
    r = client_for(anna).post(f"/api/studies/{study.pk}/images/", {"file": SimpleUploadedFile("x.png", b"nope")})
    assert r.status_code == 400


def test_readers_are_blinded_to_each_other(client_for, anna, ben, rita, image, labels):
    reading(client_for(anna), image)
    ben_client = client_for(ben)
    reading(ben_client, image, submit=False)

    tasks_seen_by_ben = ben_client.get(f"/api/images/{image.pk}/tasks/").json()
    assert [t["annotator"] for t in tasks_seen_by_ben] == ["ben"]
    anna_task = AnnotationTask.objects.get(annotator=anna)
    assert ben_client.get(f"/api/tasks/{anna_task.pk}/").status_code == 404
    ann_id = anna_task.annotations.get().pk
    assert ben_client.get(f"/api/annotations/{ann_id}/").status_code == 404

    assert len(client_for(rita).get(f"/api/images/{image.pk}/tasks/").json()) == 2


def test_review_flow_status_codes(client_for, anna, ben, rita, image, labels):
    task_id = reading(client_for(anna), image)
    assert client_for(ben).post(f"/api/tasks/{task_id}/approve/").status_code == 404  # cannot even see it
    r = client_for(rita).post(f"/api/tasks/{task_id}/reject/", {"comment": ""}, format="json")
    assert r.status_code == 409
    r = client_for(rita).post(f"/api/tasks/{task_id}/approve/", {}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    r = client_for(anna).post(f"/api/tasks/{task_id}/annotations/", BOX, format="json")
    assert r.status_code == 409


def test_bad_geometry_is_400(client_for, anna, image, labels):
    c = client_for(anna)
    task = c.post(f"/api/images/{image.pk}/tasks/").json()
    r = c.post(f"/api/tasks/{task['id']}/annotations/", {**BOX, "geometry": [0, 0, 500, 10]}, format="json")
    assert r.status_code == 400
    r = c.post(f"/api/tasks/{task['id']}/annotations/", {**BOX, "label": "unknown"}, format="json")
    assert r.status_code == 400


def test_agreement_endpoint(client_for, anna, ben, rita, image, labels):
    a = reading(client_for(anna), image)
    b = reading(client_for(ben), image, ann={**BOX, "geometry": [12, 10, 20, 20]})
    r = client_for(rita).get(f"/api/images/{image.pk}/agreement/?task_a={a}&task_b={b}")
    assert r.status_code == 200
    row = r.json()["labels"][0]
    assert row["label"] == "tumour"
    assert row["iou"] == pytest.approx(360 / 440, abs=1e-3)
    assert row["f1"] == 1.0
    assert client_for(anna).get(f"/api/images/{image.pk}/agreement/?task_a={a}&task_b={b}").status_code == 403


def test_agreement_refuses_drafts(client_for, anna, ben, rita, image, labels):
    a = reading(client_for(anna), image)
    b = reading(client_for(ben), image, submit=False)
    assert client_for(rita).get(f"/api/images/{image.pk}/agreement/?task_a={a}&task_b={b}").status_code == 409


def test_coco_export_contains_only_approved(client_for, anna, ben, rita, image, labels):
    approved = reading(client_for(anna), image)
    reading(client_for(ben), image)  # submitted, never approved
    client_for(rita).post(f"/api/tasks/{approved}/approve/", {}, format="json")

    coco = client_for(rita).get(f"/api/studies/{image.study_id}/export/coco/").json()
    assert len(coco["images"]) == 1
    assert len(coco["annotations"]) == 1
    ann = coco["annotations"][0]
    assert ann["attributes"]["annotator"] == "anna"
    assert ann["bbox"] == [10, 10, 20, 20]
    assert ann["area"] == 400
    assert {c["name"] for c in coco["categories"]} == {"tumour", "artefact"}
    assert client_for(anna).get(f"/api/studies/{image.study_id}/export/coco/").status_code == 403


def test_audit_endpoints(client_for, anna, rita, image, labels):
    reading(client_for(anna), image)
    r = client_for(rita).get("/api/audit/verify/")
    assert r.json()["ok"] is True and r.json()["checked"] >= 3
    assert client_for(anna).get("/api/audit/").status_code == 403
