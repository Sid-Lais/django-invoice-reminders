"""Invoice lifecycle: send, mark paid, void - plus shared email rendering."""

from __future__ import annotations

import logging

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from ..models import Invoice
from .email import EmailAttachment, EmailMessage, EmailSendError, Mailer, SendResult, get_mailer
from .pdf import render_invoice_pdf

logger = logging.getLogger(__name__)

CATEGORY_INVOICE = "invoice"
CATEGORY_RECEIPT = "payment-receipt"


class InvoiceStateError(Exception):
    """The requested transition is not allowed for the invoice's status."""


def email_context(invoice: Invoice, **extra) -> dict:
    return {
        "invoice": invoice,
        "client": invoice.client,
        "line_items": list(invoice.line_items.all()),
        "subtotal_display": invoice.format_money(invoice.subtotal),
        "tax_display": invoice.format_money(invoice.tax_amount),
        "total_display": invoice.total_display,
        "pdf_filename": invoice.pdf_filename,
        "business_name": settings.BUSINESS_NAME,
        **extra,
    }


def build_message(
    invoice: Invoice,
    *,
    template: str,
    subject: str,
    category: str,
    context: dict | None = None,
    attach_pdf: bool = True,
) -> EmailMessage:
    ctx = context if context is not None else email_context(invoice)
    attachments = []
    if attach_pdf:
        attachments.append(
            EmailAttachment(filename=invoice.pdf_filename, content=render_invoice_pdf(invoice))
        )
    return EmailMessage(
        to_email=invoice.client.email,
        to_name=invoice.client.name,
        subject=subject,
        text=render_to_string(f"{template}.txt", ctx).strip() + "\n",
        html=render_to_string(f"{template}.html", ctx),
        category=category,
        attachments=attachments,
    )


def _require_line_items(invoice: Invoice) -> None:
    if not invoice.line_items.exists():
        raise InvoiceStateError(f"{invoice.number} has no line items.")


def send_invoice(invoice: Invoice, mailer: Mailer | None = None) -> SendResult:
    """Email a draft invoice (PDF attached) and mark it sent.

    On failure the invoice stays draft and EmailSendError propagates.
    """
    if invoice.status != Invoice.Status.DRAFT:
        raise InvoiceStateError(f"{invoice.number} is {invoice.status}, not draft.")
    _require_line_items(invoice)
    message = build_message(
        invoice,
        template="emails/invoice",
        subject=f"Invoice {invoice.number} from {settings.BUSINESS_NAME} – {invoice.total_display}",
        category=CATEGORY_INVOICE,
    )
    result = (mailer or get_mailer()).send(message)
    invoice.status = Invoice.Status.SENT
    invoice.sent_at = timezone.now()
    invoice.save(update_fields=["status", "sent_at"])
    return result


def resend_invoice(invoice: Invoice, mailer: Mailer | None = None) -> SendResult:
    """Re-email an already-sent invoice. Status and sent_at are unchanged."""
    if invoice.status != Invoice.Status.SENT:
        raise InvoiceStateError(f"{invoice.number} is {invoice.status}, not sent.")
    _require_line_items(invoice)
    message = build_message(
        invoice,
        template="emails/invoice",
        subject=f"Invoice {invoice.number} from {settings.BUSINESS_NAME} – {invoice.total_display}",
        category=CATEGORY_INVOICE,
        context=email_context(invoice, is_resend=True),
    )
    return (mailer or get_mailer()).send(message)


def mark_paid(invoice: Invoice, mailer: Mailer | None = None) -> SendResult:
    """Mark a sent invoice paid (stopping reminders), then email a receipt.

    The paid status is saved before the receipt is sent: if the receipt
    fails, the invoice is still paid and EmailSendError propagates.
    """
    if invoice.status != Invoice.Status.SENT:
        raise InvoiceStateError(f"{invoice.number} is {invoice.status}, not sent.")
    invoice.status = Invoice.Status.PAID
    invoice.paid_at = timezone.now()
    invoice.save(update_fields=["status", "paid_at"])
    message = build_message(
        invoice,
        template="emails/payment_receipt",
        subject=f"Payment received for invoice {invoice.number} – thank you",
        category=CATEGORY_RECEIPT,
        context=email_context(invoice, paid_on=invoice.client.local_date(invoice.paid_at)),
    )
    return (mailer or get_mailer()).send(message)


def void_invoice(invoice: Invoice) -> None:
    if invoice.status == Invoice.Status.PAID:
        raise InvoiceStateError(f"{invoice.number} is already paid.")
    if invoice.status == Invoice.Status.VOID:
        raise InvoiceStateError(f"{invoice.number} is already void.")
    invoice.status = Invoice.Status.VOID
    invoice.save(update_fields=["status"])


__all__ = [
    "EmailSendError",
    "InvoiceStateError",
    "build_message",
    "email_context",
    "mark_paid",
    "resend_invoice",
    "send_invoice",
    "void_invoice",
]
