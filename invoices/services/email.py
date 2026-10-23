"""Email layer: a tiny Mailer protocol with a Mailtrap and an in-memory fake.

Select the implementation with settings.MAILER_CLASS. Tests use FakeMailer,
so they never touch the network.
"""

from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass, field
from typing import Protocol

import mailtrap as mt
from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content: bytes
    mimetype: str = "application/pdf"


@dataclass
class EmailMessage:
    to_email: str
    subject: str
    text: str
    html: str
    category: str
    to_name: str = ""
    attachments: list[EmailAttachment] = field(default_factory=list)


@dataclass(frozen=True)
class SendResult:
    message_id: str


class EmailSendError(Exception):
    """Raised when an email could not be handed over to the provider."""


class Mailer(Protocol):
    def send(self, message: EmailMessage) -> SendResult: ...


def build_mail(message: EmailMessage) -> mt.Mail:
    """Translate our EmailMessage into the Mailtrap SDK's Mail object."""
    sender = mt.Address(email=settings.MAIL_FROM_EMAIL, name=settings.MAIL_FROM_NAME or None)
    recipient = mt.Address(email=message.to_email, name=message.to_name or None)
    attachments = [
        mt.Attachment(
            content=base64.b64encode(attachment.content),
            filename=attachment.filename,
            disposition=mt.Disposition.ATTACHMENT,
            mimetype=attachment.mimetype,
        )
        for attachment in message.attachments
    ]
    return mt.Mail(
        sender=sender,
        to=[recipient],
        subject=message.subject,
        text=message.text,
        html=message.html,
        category=message.category,
        attachments=attachments or None,
    )


class MailtrapMailer:
    """Sends through the Mailtrap Email API, transactional stream."""

    def __init__(self, token: str | None = None):
        self._client = mt.MailtrapClient(token=token or settings.MAILTRAP_API_TOKEN)

    def send(self, message: EmailMessage) -> SendResult:
        mail = build_mail(message)
        try:
            response = self._client.send(mail)
        except mt.AuthorizationError as exc:
            raise EmailSendError(f"Mailtrap rejected the API token: {exc}") from exc
        except mt.APIError as exc:
            raise EmailSendError(f"Mailtrap API error ({exc.status}): {exc}") from exc
        except Exception as exc:  # network errors, timeouts, SDK validation
            raise EmailSendError(f"{type(exc).__name__}: {exc}") from exc

        if not response.get("success"):
            raise EmailSendError(f"Mailtrap did not accept the message: {response}")
        message_ids = response.get("message_ids") or []
        message_id = message_ids[0] if message_ids else ""
        logger.info(
            "Sent email category=%s to=%s message_id=%s",
            message.category,
            message.to_email,
            message_id,
        )
        return SendResult(message_id=message_id)


class FakeMailer:
    """Captures the exact Mailtrap API payload instead of sending it.

    `outbox` and `fail_with` are class-level so every instance created via
    get_mailer() shares them; tests reset them between runs.
    """

    outbox: list[dict] = []
    fail_with: str | None = None

    def send(self, message: EmailMessage) -> SendResult:
        if FakeMailer.fail_with:
            raise EmailSendError(FakeMailer.fail_with)
        payload = build_mail(message).api_data
        message_id = f"fake-{uuid.uuid4()}"
        FakeMailer.outbox.append({**payload, "message_id": message_id})
        return SendResult(message_id=message_id)

    @classmethod
    def reset(cls) -> None:
        cls.outbox = []
        cls.fail_with = None


def get_mailer() -> Mailer:
    return import_string(settings.MAILER_CLASS)()
