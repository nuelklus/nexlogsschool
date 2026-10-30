"""Fee-reminder integration points. Nothing here sends a message by default.

``send_reminders`` only calls a channel when (a) the caller passes ``confirm=True``
(an explicit authorised action) and (b) ``settings.FINANCE_REMINDER_CHANNELS`` maps
the channel to a dotted path of a backend class. Without that configuration every
candidate is reported as ``skipped``.
"""

from django.conf import settings
from django.utils.module_loading import import_string

from core.models import StudentGuardian

from .reports import outstanding_by_student


class ReminderChannel:
    """Implement ``send(phone, message)`` for SMS or WhatsApp providers."""

    def send(self, phone, message):
        raise NotImplementedError


def reminder_candidates(tenant_id, *, overdue_only=True):
    rows = outstanding_by_student(tenant_id)
    candidates = []
    for row in rows:
        if overdue_only and row["overdue_balance"] == "0.00":
            continue
        guardians = StudentGuardian.objects.filter(tenant_id=tenant_id, student_id=row["student"]).select_related("user")
        phones = [g.user.phone for g in guardians if getattr(g.user, "phone", "")]
        candidates.append({**row, "phones": phones})
    return candidates


def send_reminders(tenant_id, channel, *, confirm=False, overdue_only=True):
    configured = getattr(settings, "FINANCE_REMINDER_CHANNELS", {}) or {}
    path = configured.get(channel)
    results = []
    for candidate in reminder_candidates(tenant_id, overdue_only=overdue_only):
        if not confirm or not path:
            reason = "Not confirmed." if not confirm else f"No {channel} provider is configured."
            results.append({"student": candidate["student"], "status": "skipped", "reason": reason})
            continue
        backend = import_string(path)()
        message = f"Fee reminder: GHS {candidate['overdue_balance']} is overdue for {candidate['student_name']}."
        for phone in candidate["phones"]:
            backend.send(phone, message)
            results.append({"student": candidate["student"], "status": "sent", "to": phone})
    return results
