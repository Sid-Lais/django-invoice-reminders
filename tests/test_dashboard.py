import datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from invoices.models import Client, Invoice
from invoices.services.email import EmailSendError, FakeMailer

pytestmark = pytest.mark.django_db


def payload(**overrides):
    data = {
        "name": "Website redesign",
        "amount": "1200.50",
        "currency": "USD",
        "due_date": (datetime.date.today() + datetime.timedelta(days=14)).isoformat(),
        "client_email": "Priya@Example.com",
        "client_name": "Priya Nair",
        "send_now": "on",
    }
    data.update(overrides)
    return data


def test_empty_dashboard_shows_the_form(client):
    page = client.get(reverse("dashboard")).content.decode()
    assert "Invoice name" in page and "Send to" in page and "No invoices yet" in page


def test_create_stores_invoice_and_emails_the_client(client, outbox):
    response = client.post(reverse("invoice-create"), payload(), follow=True)
    invoice = Invoice.objects.get()
    assert invoice.status == Invoice.Status.SENT and invoice.total == Decimal("1200.50")
    assert invoice.client.email == "priya@example.com" and invoice.client.name == "Priya Nair"
    assert invoice.line_items.get().description == "Website redesign"
    (message,) = outbox
    assert message["to"] == [{"email": "priya@example.com", "name": "Priya Nair"}]
    assert message["category"] == "invoice"
    page = response.content.decode()
    assert invoice.number in page and "Website redesign" in page and "USD 1,200.50" in page


def test_save_as_draft_sends_nothing(client, outbox):
    client.post(reverse("invoice-create"), payload(send_now=""))
    assert Invoice.objects.get().status == Invoice.Status.DRAFT and outbox == []


def test_existing_client_is_reused_by_email(client, outbox):
    client.post(reverse("invoice-create"), payload(client_email="a@example.com"))
    client.post(reverse("invoice-create"), payload(client_email="A@example.com", name="Hosting"))
    assert Client.objects.count() == 1 and Invoice.objects.count() == 2


def test_missing_client_name_falls_back_to_email(client, outbox):
    client.post(reverse("invoice-create"), payload(client_name="", client_email="sam.lee@example.com"))
    assert Client.objects.get().name == "Sam Lee"


@pytest.mark.parametrize(
    "bad",
    [
        {"amount": "0"},
        {"amount": "abc"},
        {"name": ""},
        {"client_email": "not-an-email"},
        {"due_date": "2020-01-01"},
    ],
)
def test_invalid_input_is_rejected_with_a_message(client, outbox, bad):
    response = client.post(reverse("invoice-create"), payload(**bad))
    assert response.status_code == 200 and "Please fix the highlighted fields" in response.content.decode()
    assert Invoice.objects.count() == 0 and outbox == []


def test_failed_email_keeps_the_draft(client, monkeypatch):
    def boom(self, message):
        raise EmailSendError("provider down")

    monkeypatch.setattr(FakeMailer, "send", boom)
    page = client.post(reverse("invoice-create"), payload(), follow=True).content.decode()
    assert Invoice.objects.get().status == Invoice.Status.DRAFT and "provider down" in page


def test_actions_send_mark_paid_and_void(client, outbox):
    client.post(reverse("invoice-create"), payload(send_now=""))
    invoice = Invoice.objects.get()
    client.post(reverse("invoice-action", args=[invoice.pk, "send"]))
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.SENT
    client.post(reverse("invoice-action", args=[invoice.pk, "paid"]))
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID and len(outbox) == 2
    client.post(reverse("invoice-action", args=[invoice.pk, "void"]))
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID  # paid invoices can't be voided


def test_actions_only_accept_post(client):
    invoice = Invoice.objects.create(
        client=Client.objects.create(name="X", email="x@example.com"),
        due_date=datetime.date.today() + datetime.timedelta(days=5),
    )
    url = reverse("invoice-action", args=[invoice.pk, "void"])
    assert client.get(url).status_code == 405
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.DRAFT


def test_due_date_field_is_prefilled_in_iso_format(client):
    page = client.get(reverse("dashboard")).content.decode()
    due = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    assert f'name="due_date" id="id_due_date" value="{due}"' in page


def test_table_shows_relative_due_date_and_reminder_tracker(client, outbox):
    client.post(reverse("invoice-create"), payload())
    page = client.get(reverse("dashboard")).content.decode()
    assert "in 14 days" in page
    assert page.count('class="dot ') >= 4  # one dot per reminder stage
    assert 'data-filter="outstanding"' in page
