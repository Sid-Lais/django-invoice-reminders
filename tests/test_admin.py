import datetime

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from invoices.models import Client, Invoice
from invoices.services.email import FakeMailer

from .conftest import categories

DUE = datetime.date(2026, 3, 10)
CHANGELIST = "admin:invoices_invoice_changelist"


@pytest.fixture
def admin_client_logged_in(client, db):
    user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
    client.force_login(user)
    return client


def run_action(http, action, *objs, url=CHANGELIST):
    response = http.post(
        reverse(url),
        {"action": action, "_selected_action": [obj.pk for obj in objs]},
        follow=True,
    )
    assert response.status_code == 200
    return [str(m) for m in response.context["messages"]]


def test_send_invoice_action(admin_client_logged_in, make_invoice, outbox):
    invoice = make_invoice(due_date=DUE, status="draft")
    messages = run_action(admin_client_logged_in, "send_invoice_action", invoice)
    invoice.refresh_from_db()
    assert invoice.status == "sent"
    assert categories(outbox) == ["invoice"]
    assert outbox[0]["attachments"][0]["filename"] == f"Invoice-{invoice.number}.pdf"
    assert "Sent 1 invoice." in messages


def test_send_invoice_action_failure_keeps_draft(admin_client_logged_in, make_invoice, outbox):
    invoice = make_invoice(due_date=DUE, status="draft")
    FakeMailer.fail_with = "Mailtrap API error (401): Unauthorized"
    messages = run_action(admin_client_logged_in, "send_invoice_action", invoice)
    invoice.refresh_from_db()
    assert invoice.status == "draft"
    assert outbox == []
    assert any("email failed" in m and "Unauthorized" in m for m in messages)
    assert "1 invoice failed." in messages


def test_send_action_skips_non_draft(admin_client_logged_in, make_invoice, outbox):
    sent = make_invoice(due_date=DUE)
    messages = run_action(admin_client_logged_in, "send_invoice_action", sent)
    assert outbox == []
    assert any("Skipped 1 invoice" in m for m in messages)


def test_resend_invoice_action(admin_client_logged_in, make_invoice, outbox):
    invoice = make_invoice(due_date=DUE)
    sent_at = invoice.sent_at
    run_action(admin_client_logged_in, "resend_invoice_action", invoice)
    invoice.refresh_from_db()
    assert categories(outbox) == ["invoice"] and invoice.sent_at == sent_at


def test_mark_paid_action(admin_client_logged_in, make_invoice, outbox):
    invoice = make_invoice(due_date=DUE)
    messages = run_action(admin_client_logged_in, "mark_paid_action", invoice)
    invoice.refresh_from_db()
    assert invoice.status == "paid"
    assert categories(outbox) == ["payment-receipt"]
    assert "Marked 1 invoice as paid." in messages


def test_void_action(admin_client_logged_in, make_invoice):
    invoice = make_invoice(due_date=DUE)
    run_action(admin_client_logged_in, "void_action", invoice)
    assert Invoice.objects.get(pk=invoice.pk).status == "void"


def test_pdf_view(admin_client_logged_in, make_invoice):
    invoice = make_invoice(due_date=DUE)
    url = reverse("admin:invoices_invoice_pdf", args=[invoice.pk])
    response = admin_client_logged_in.get(url)
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == f'inline; filename="Invoice-{invoice.number}.pdf"'
    assert response.content.startswith(b"%PDF")
    download = admin_client_logged_in.get(url + "?download=1")
    assert download["Content-Disposition"].startswith("attachment;")


def test_pdf_view_requires_login(client, make_invoice):
    invoice = make_invoice(due_date=DUE)
    response = client.get(reverse("admin:invoices_invoice_pdf", args=[invoice.pk]))
    assert response.status_code == 302


def test_changelists_and_change_form_render(admin_client_logged_in, make_invoice):
    invoice = make_invoice(due_date=DUE)
    Client.objects.create(name="Flagged", email="f@example.com", email_status="bounced")
    assert admin_client_logged_in.get(reverse(CHANGELIST)).status_code == 200
    page = admin_client_logged_in.get(reverse("admin:invoices_client_changelist"))
    assert b"BOUNCED" in page.content
    change = admin_client_logged_in.get(reverse("admin:invoices_invoice_change", args=[invoice.pk]))
    assert change.status_code == 200
    assert admin_client_logged_in.get(reverse("admin:invoices_invoice_add")).status_code == 200


def test_clear_email_flag_action(admin_client_logged_in):
    flagged = Client.objects.create(
        name="F", email="f@example.com", email_status="spam", email_flag_reason="spam complaint"
    )
    run_action(admin_client_logged_in, "clear_email_flag", flagged, url="admin:invoices_client_changelist")
    flagged.refresh_from_db()
    assert flagged.email_status == "ok" and flagged.email_flagged_at is None and flagged.email_flag_reason == ""


def test_last_reminder_column_uses_local_date(make_invoice):
    from django.contrib.admin.sites import site

    from invoices.models import ReminderLog

    invoice = make_invoice(due_date=DUE)
    # 19:00 UTC on 9 March is 00:30 IST on 10 March.
    ReminderLog.objects.create(
        invoice=invoice,
        stage="due_today",
        status="sent",
        scheduled_for=DUE,
        sent_at=datetime.datetime(2026, 3, 9, 19, 0, tzinfo=datetime.UTC),
    )
    model_admin = site._registry[Invoice]
    obj = model_admin.get_queryset(None).get(pk=invoice.pk)
    assert model_admin.last_reminder(obj) == "due_today (10 Mar 2026)"
