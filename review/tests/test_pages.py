from django.core.management import call_command

from review.models import AnnotationTask, AuditEvent
from review.services import audit


def test_seed_demo_is_idempotent_and_audited(db):
    call_command("seed_demo")
    call_command("seed_demo")
    assert AnnotationTask.objects.filter(status="submitted").count() == 2
    assert AuditEvent.objects.exists() and audit.verify_chain().ok


def test_pages_render_for_each_role(client, db):
    call_command("seed_demo")
    task = AnnotationTask.objects.first()

    client.login(username="anna", password="second-read-demo")
    assert client.get("/").status_code == 200
    assert client.get(f"/studies/{task.image.study_id}/").status_code == 200
    assert client.get(f"/images/{task.image_id}/annotate/").status_code == 200
    assert client.get("/review/").status_code == 403

    client.login(username="rita", password="second-read-demo")
    page = client.get("/review/")
    assert page.status_code == 200 and b"anna" in page.content
    assert client.get(f"/review/{task.pk}/").status_code == 200
    assert client.get(f"/images/{task.image_id}/annotate/").status_code == 403


def test_anonymous_is_sent_to_login(client, db):
    r = client.get("/")
    assert r.status_code == 302 and "/login/" in r["Location"]
