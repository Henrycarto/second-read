"""Writing to and verifying the hash-chained audit log."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from review.models import AuditEvent

GENESIS = "0" * 64


def record(actor, action: str, obj, payload: dict | None = None) -> AuditEvent:
    """
    Append one event. Must run inside the same transaction as the change it describes,
    so the log and the data can never disagree. The row lock on the last event
    serialises writers, keeping the chain linear under concurrency.
    """
    with transaction.atomic():
        last = AuditEvent.objects.select_for_update().order_by("-id").first()
        event = AuditEvent(
            actor=actor,
            action=action,
            object_type=type(obj).__name__,
            object_id=str(obj.pk),
            payload=payload or {},
            created_at=timezone.now(),
            prev_hash=last.hash if last else GENESIS,
        )
        event.hash = event.compute_hash()
        event.save()
        return event


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    checked: int
    first_bad_id: int | None = None
    reason: str = ""


def verify_chain() -> ChainReport:
    prev = GENESIS
    checked = 0
    for event in AuditEvent.objects.order_by("id").iterator():
        if event.prev_hash != prev:
            return ChainReport(False, checked, event.id, "previous hash does not match (event removed or reordered)")
        if event.compute_hash() != event.hash:
            return ChainReport(False, checked, event.id, "content does not match its hash (event modified)")
        prev = event.hash
        checked += 1
    return ChainReport(True, checked)
