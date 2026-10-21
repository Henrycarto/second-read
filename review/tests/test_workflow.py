import pytest

from review.models import AnnotationTask, AuditEvent
from review.services import audit, workflow
from review.services.workflow import WorkflowError

BOX = [10, 10, 20, 20]


@pytest.fixture
def task(image, anna):
    return workflow.start_task(image, anna)


def test_full_happy_path_is_audited(task, anna, rita, labels):
    workflow.add_annotation(task, anna, label=labels["tumour"], shape="box", geometry=BOX)
    workflow.submit(task, anna)
    workflow.decide(task, rita, "approve")
    task.refresh_from_db()
    assert task.status == AnnotationTask.Status.APPROVED
    actions = list(AuditEvent.objects.values_list("action", flat=True))
    assert actions == ["task.created", "annotation.created", "task.submitted", "task.approved"]
    assert audit.verify_chain().ok


def test_reviewer_cannot_review_own_task(image, rita, labels):
    rita.groups.add(*anna_groups())
    own = workflow.start_task(image, rita)
    workflow.add_annotation(own, rita, label=labels["tumour"], shape="box", geometry=BOX)
    workflow.submit(own, rita)
    with pytest.raises(WorkflowError, match="own task") as e:
        workflow.decide(own, rita, "approve")
    assert e.value.forbidden


def anna_groups():
    from django.contrib.auth.models import Group
    return [Group.objects.get_or_create(name="annotator")[0]]


def test_superuser_is_still_bound_by_four_eyes(image, anna, labels, django_user_model):
    boss = django_user_model.objects.create_superuser("boss", password="pw-not-used-2")
    own = workflow.start_task(image, boss)
    workflow.add_annotation(own, boss, label=labels["tumour"], shape="box", geometry=BOX)
    workflow.submit(own, boss)
    with pytest.raises(WorkflowError, match="own task"):
        workflow.decide(own, boss, "approve")


def test_annotator_cannot_decide(task, anna, ben, labels):
    workflow.add_annotation(task, anna, label=labels["tumour"], shape="box", geometry=BOX)
    workflow.submit(task, anna)
    with pytest.raises(WorkflowError) as e:
        workflow.decide(task, ben, "approve")
    assert e.value.forbidden


def test_cannot_submit_empty_task(task, anna):
    with pytest.raises(WorkflowError, match="no annotations"):
        workflow.submit(task, anna)


def test_submitted_task_is_frozen(task, anna, labels):
    ann = workflow.add_annotation(task, anna, label=labels["tumour"], shape="box", geometry=BOX)
    workflow.submit(task, anna)
    with pytest.raises(WorkflowError, match="cannot be edited"):
        workflow.add_annotation(task, anna, label=labels["tumour"], shape="box", geometry=BOX)
    with pytest.raises(WorkflowError, match="cannot be edited"):
        workflow.update_annotation(ann, anna, geometry=[0, 0, 5, 5])
    with pytest.raises(WorkflowError, match="cannot be edited"):
        workflow.delete_annotation(ann, anna)


def test_other_annotator_cannot_edit(task, anna, ben, labels):
    ann = workflow.add_annotation(task, anna, label=labels["tumour"], shape="box", geometry=BOX)
    with pytest.raises(WorkflowError) as e:
        workflow.update_annotation(ann, ben, geometry=[0, 0, 5, 5])
    assert e.value.forbidden


def test_reject_needs_comment_then_reader_can_fix_and_resubmit(task, anna, rita, labels):
    ann = workflow.add_annotation(task, anna, label=labels["tumour"], shape="box", geometry=BOX)
    workflow.submit(task, anna)
    with pytest.raises(WorkflowError, match="comment"):
        workflow.decide(task, rita, "reject", "  ")
    workflow.decide(task, rita, "reject", "Region misses the lower margin")
    workflow.update_annotation(ann, anna, geometry=[10, 10, 20, 40])
    workflow.submit(task, anna)
    workflow.decide(task, rita, "approve")
    task.refresh_from_db()
    assert task.status == AnnotationTask.Status.APPROVED
    assert task.reviews.count() == 2


def test_cannot_decide_twice(task, anna, rita, labels):
    workflow.add_annotation(task, anna, label=labels["tumour"], shape="box", geometry=BOX)
    workflow.submit(task, anna)
    workflow.decide(task, rita, "approve")
    with pytest.raises(WorkflowError, match="only submitted"):
        workflow.decide(task, rita, "reject", "changed my mind")


def test_update_is_audited_with_before_and_after(task, anna, labels):
    ann = workflow.add_annotation(task, anna, label=labels["tumour"], shape="box", geometry=BOX)
    workflow.update_annotation(ann, anna, geometry=[0, 0, 5, 5])
    event = AuditEvent.objects.filter(action="annotation.updated").get()
    assert event.payload["before"]["geometry"] == BOX
    assert event.payload["after"]["geometry"] == [0, 0, 5, 5]


def test_invalid_geometry_never_reaches_the_database(task, anna, labels):
    with pytest.raises(ValueError):
        workflow.add_annotation(task, anna, label=labels["tumour"], shape="box", geometry=[0, 0, 9999, 10])
    assert task.annotations.count() == 0
    assert not AuditEvent.objects.filter(action="annotation.created").exists()


def test_starting_twice_returns_the_same_task(image, anna):
    assert workflow.start_task(image, anna).pk == workflow.start_task(image, anna).pk
