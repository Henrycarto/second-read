import pytest
from django.db import connection

from review.models import AuditEvent
from review.services import audit


@pytest.fixture
def three_events(rita, study):
    return [audit.record(rita, f"test.{i}", study, {"i": i}) for i in range(3)]


def test_chain_verifies(three_events):
    report = audit.verify_chain()
    assert report.ok and report.checked == 3
    assert three_events[1].prev_hash == three_events[0].hash


def test_orm_refuses_changes(three_events):
    event = three_events[0]
    event.action = "edited"
    with pytest.raises(PermissionError):
        event.save()
    with pytest.raises(PermissionError):
        event.delete()
    with pytest.raises(PermissionError):
        AuditEvent.objects.all().update(action="x")
    with pytest.raises(PermissionError):
        AuditEvent.objects.all().delete()


def test_raw_sql_edit_is_detected(three_events):
    with connection.cursor() as cur:
        cur.execute("UPDATE review_auditevent SET payload = %s WHERE id = %s", ['{"i": 99}', three_events[1].id])
    report = audit.verify_chain()
    assert not report.ok
    assert report.first_bad_id == three_events[1].id
    assert "modified" in report.reason


def test_raw_sql_delete_is_detected(three_events):
    with connection.cursor() as cur:
        cur.execute("DELETE FROM review_auditevent WHERE id = %s", [three_events[1].id])
    report = audit.verify_chain()
    assert not report.ok
    assert report.first_bad_id == three_events[2].id
    assert "removed" in report.reason
