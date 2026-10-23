import base64
import datetime
from decimal import Decimal

import pytest

from invoices.models import Invoice, LineItem
from invoices.services.email import EmailSendError, FakeMailer
from invoices.services.invoicing import (
    InvoiceStateError,
    mark_paid,
    send_invoice,
    void_invoice,
)
from invoices.services.pdf import render_invoice_pdf

from .conftest import categories

DUE = datetime.date(2026, 3, 10)


def test_auto_numbering_per_year(client_obj):
    first = Invoice.objects.create(client=client_obj, issue_date=datetime.date(2026, 1, 5), due_date=DUE)
    second = Invoice.objects.create(client=client_obj, issue_date=datetime.date(2026, 2, 1), due_date=DUE)
    other_year = Invoice.objects.create(
        client=client_obj, issue_date=datetime.date(2027, 1, 1), due_date=datetime.date(2027, 2, 1)
    )
    explicit = Invoice.objects.create(client=client_obj, number="CUSTOM-1", due_date=DUE)
    assert (first.number, second.number, other_year.number, explicit.number) == (
        "INV-2026-0001",
        "INV-2026-0002",
        "INV-2027-0001",
        "CUSTOM-1",
    )


def test_totals_are_quantized(make_invoice):
    invoice = make_invoice(
        due_date=DUE, items=[("A", "3", "33.33"), ("B", "0.5", "10.01")], tax_rate="18"
    )
    assert invoice.subtotal == Decimal("105.00")  # 99.99 + 5.005 -> 5.01
    assert invoice.tax_amount == Decimal("18.90")
    assert invoice.total == Decimal("123.90")
    assert invoice.total_display == "USD 123.90"
    assert LineItem.objects.first().amount == Decimal("99.99")


def test_days_overdue(make_invoice):
    invoice = make_invoice(due_date=DUE)
    assert invoice.days_overdue_on(DUE - datetime.timedelta(days=2)) == 0
    assert invoice.days_overdue_on(DUE + datetime.timedelta(days=9)) == 9
    invoice.status = Invoice.Status.PAID
    assert invoice.days_overdue_on(DUE + datetime.timedelta(days=9)) == 0


def test_pdf_is_valid_with_unicode_and_thousands_separators(make_invoice, client_obj):
    client_obj.name = "Zoë Ångström – Café ₹"
    client_obj.save()
    invoice = make_invoice(due_date=DUE, items=[("Développement — ünïcode ✓", "1", "1234567.50")], tax_rate="0")
    pdf = render_invoice_pdf(invoice)
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 2000
    assert invoice.format_money(invoice.total) == "USD 1,234,567.50"
    assert invoice.pdf_filename == f"Invoice-{invoice.number}.pdf"


def test_send_invoice_attaches_pdf_and_marks_sent(make_invoice, outbox):
    invoice = make_invoice(due_date=DUE, status="draft")
    send_invoice(invoice)
    invoice.refresh_from_db()
    assert invoice.status == "sent" and invoice.sent_at is not None

    (payload,) = outbox
    assert payload["category"] == "invoice"
    assert payload["from"] == {"email": "billing@example.com", "name": "Invoices"}
    assert invoice.number in payload["subject"]
    assert "USD 3,540.00" in payload["text"] and "USD 3,540.00" in payload["html"]
    for body in (payload["text"], payload["html"]):
        assert "How to pay" not in body and "IBAN" not in body and "SWIFT" not in body
    (attachment,) = payload["attachments"]
    assert attachment["filename"] == f"Invoice-{invoice.number}.pdf"
    assert attachment["type"] == "application/pdf"
    assert attachment["disposition"] == "attachment"
    assert base64.b64decode(attachment["content"]).startswith(b"%PDF")


def test_send_failure_keeps_draft(make_invoice, outbox):
    invoice = make_invoice(due_date=DUE, status="draft")
    FakeMailer.fail_with = "network down"
    with pytest.raises(EmailSendError):
        send_invoice(invoice)
    invoice.refresh_from_db()
    assert invoice.status == "draft" and invoice.sent_at is None


def test_send_requires_line_items(make_invoice):
    invoice = make_invoice(due_date=DUE, status="draft", items=())
    with pytest.raises(InvoiceStateError, match="no line items"):
        send_invoice(invoice)


def test_mark_paid_sends_receipt(make_invoice, outbox):
    invoice = make_invoice(due_date=DUE)
    mark_paid(invoice)
    invoice.refresh_from_db()
    assert invoice.status == "paid" and invoice.paid_at is not None
    assert categories(outbox) == ["payment-receipt"]
    assert outbox[0]["attachments"][0]["filename"] == f"Invoice-{invoice.number}.pdf"


def test_mark_paid_only_from_sent(make_invoice):
    with pytest.raises(InvoiceStateError):
        mark_paid(make_invoice(due_date=DUE, status="draft"))


def test_void(make_invoice):
    invoice = make_invoice(due_date=DUE)
    void_invoice(invoice)
    assert Invoice.objects.get(pk=invoice.pk).status == "void"
    with pytest.raises(InvoiceStateError):
        void_invoice(invoice)


def test_default_currency_is_usd(client_obj):
    assert Invoice.objects.create(client=client_obj, due_date=DUE).currency == "USD"


@pytest.mark.parametrize("currency", ["USD", "EUR"])
def test_email_shows_invoice_currency(make_invoice, outbox, currency):
    send_invoice(make_invoice(due_date=DUE, status="draft", currency=currency))
    assert f"{currency} 3,540.00" in outbox[0]["text"] and f"{currency} 3,540.00" in outbox[0]["html"]


def test_emails_contain_no_email_addresses_or_postal_address(make_invoice, outbox):
    invoice = make_invoice(due_date=DUE, status="draft")
    send_invoice(invoice)
    mark_paid(invoice)
    for payload in outbox:
        for body in (payload["text"], payload["html"]):
            assert "@" not in body, body
            assert "mailto:" not in body


def test_pdf_shows_no_email_address_payment_details_or_notes(make_invoice, monkeypatch):
    from fpdf import FPDF

    drawn = []
    original = FPDF.normalize_text

    def spy(self, text):
        drawn.append(text)
        return original(self, text)

    monkeypatch.setattr(FPDF, "normalize_text", spy)
    invoice = make_invoice(due_date=DUE)
    invoice.notes = "Internal note: client prefers Friday calls"
    invoice.save()
    render_invoice_pdf(invoice)
    page = "\n".join(drawn)
    assert "Invoices" in page and "Rao & Co" in page
    assert "PAYMENT" not in page.upper()
    assert "Internal note" not in page and "NOTES" not in page.upper()
    assert "@" not in page


def test_client_facing_text_never_says_automated(make_invoice, outbox):
    from invoices.services.reminders import run_reminders

    invoice = make_invoice(due_date=DUE)
    for offset in (-3, 0, 7, 14):
        run_reminders(DUE + datetime.timedelta(days=offset))
    mark_paid(invoice)
    assert len(outbox) == 5
    for payload in outbox:
        for text in (payload["subject"], payload["text"], payload["html"]):
            assert "automat" not in text.lower()
            assert "hear from you" not in text.lower()
