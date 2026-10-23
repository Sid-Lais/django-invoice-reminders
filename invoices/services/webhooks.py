"""Process Mailtrap webhook events idempotently."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import Client, WebhookEvent

logger = logging.getLogger(__name__)

# Mailtrap event name -> client email_status. "bounce" is a hard bounce;
# "soft bounce", delivery, open, click, etc. are stored but not acted on.
FLAGGING_EVENTS = {
    "bounce": Client.EmailStatus.BOUNCED,
    "spam": Client.EmailStatus.SPAM,
}


@dataclass
class WebhookResult:
    received: int = 0
    processed: int = 0
    duplicates: int = 0
    flagged_clients: int = 0


def event_identifier(event: dict) -> str:
    event_id = event.get("event_id")
    if event_id:
        return str(event_id)[:255]
    # Fallback for events without an id: a stable hash of the event itself.
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _flag_reason(event: dict, event_type: str) -> str:
    details = [
        str(event[key])
        for key in ("reason", "response", "bounce_category", "response_code")
        if event.get(key) not in (None, "")
    ]
    reason = f"Mailtrap '{event_type}' event"
    if event.get("message_id"):
        reason += f" for message {event['message_id']}"
    return reason + (": " + "; ".join(details) if details else "")


def process_events(events: list) -> WebhookResult:
    result = WebhookResult(received=len(events))
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event") or "").strip().lower()
        email = str(event.get("email") or "").strip()
        try:
            with transaction.atomic():
                WebhookEvent.objects.create(
                    event_id=event_identifier(event),
                    event_type=event_type[:50],
                    email=email[:254],
                    payload=event,
                )
                result.processed += 1
                status = FLAGGING_EVENTS.get(event_type)
                if status and email:
                    flagged = Client.objects.filter(email__iexact=email).update(
                        email_status=status,
                        email_flagged_at=timezone.now(),
                        email_flag_reason=_flag_reason(event, event_type),
                    )
                    result.flagged_clients += flagged
                    if flagged:
                        logger.warning("Flagged %s client(s) as %s: %s", flagged, status, email)
        except IntegrityError:
            result.duplicates += 1
    return result
