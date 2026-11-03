
import datetime

import pytest
from django.core import signing
from django.urls import reverse

from invoices.services.signed_links import (
    SALT,
    generate_invoice_token,
)


@pytest.mark.django_db
def test_client_can_view_invoice(make_invoice, pytest_client):
    invoice = make_invoice(
        due_date=datetime.date(2026, 3, 10),
    )

    token = generate_invoice_token(invoice.pk)

    response = pytest_client.get(
        reverse("client-invoice", kwargs={"token": token})
    )

    assert response.status_code == 200
    assert invoice.number in response.content.decode()
    assert invoice.client.name in response.content.decode()


@pytest.fixture
def pytest_client():
    from django.test import Client

    return Client()


@pytest.mark.django_db
def test_invalid_token_shows_error_page(pytest_client):
    response = pytest_client.get(
        reverse("client-invoice", kwargs={"token": "invalid-token"})
    )

    assert response.status_code == 400
    assert b"Invoice Link Unavailable" in response.content


@pytest.mark.django_db
def test_expired_token_shows_error_page(make_invoice, pytest_client):
    invoice = make_invoice(
        due_date=datetime.date(2026, 3, 10),
    )

    from unittest.mock import patch

    with patch("django.core.signing.time.time", return_value=1):
        token = generate_invoice_token(invoice.pk)

    response = pytest_client.get(
        reverse("client-invoice", kwargs={"token": token})
    )

    assert response.status_code == 400
    assert b"Invoice Link Unavailable" in response.content

@pytest.mark.django_db
def test_missing_invoice_shows_error_page(pytest_client):
    token = signing.dumps(
        {"invoice_id": 999999},
        salt="invoices.client-links",
    )

    response = pytest_client.get(
        reverse("client-invoice", kwargs={"token": token})
    )

    assert response.status_code == 404
    assert b"Invoice Link Unavailable" in response.content

@pytest.mark.django_db
def test_client_can_dispute_invoice(make_invoice, pytest_client, outbox):
    invoice = make_invoice(
        due_date=datetime.date(2026, 3, 10),
    )
    invoice.status = "sent"
    invoice.save(update_fields=["status"])

    token = generate_invoice_token(invoice.pk)

    response = pytest_client.post(
        reverse("client-invoice-dispute", kwargs={"token": token})
    )

    invoice.refresh_from_db()

    assert response.status_code == 200
    assert invoice.is_disputed is True
    assert b"currently under dispute" in response.content


@pytest.mark.django_db
def test_dispute_notifies_owner(make_invoice, pytest_client, outbox, settings):
    settings.OWNER_EMAIL = "owner@example.com"

    invoice = make_invoice(
        due_date=datetime.date(2026, 3, 10),
    )
    invoice.status = "sent"
    invoice.save(update_fields=["status"])

    token = generate_invoice_token(invoice.pk)

    pytest_client.post(
        reverse("client-invoice-dispute", kwargs={"token": token})
    )

    assert len(outbox) == 1
    assert outbox[0]["to"][0]["email"] == "owner@example.com"
    assert invoice.number in outbox[0]["subject"]


@pytest.mark.django_db
def test_repeated_dispute_does_not_send_duplicate_email(
    make_invoice, pytest_client, outbox, settings
):
    settings.OWNER_EMAIL = "owner@example.com"

    invoice = make_invoice(
        due_date=datetime.date(2026, 3, 10),
    )
    invoice.status = "sent"
    invoice.save(update_fields=["status"])

    token = generate_invoice_token(invoice.pk)
    url = reverse("client-invoice-dispute", kwargs={"token": token})

    first_response = pytest_client.post(url)
    second_response = pytest_client.post(url)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert len(outbox) == 1


@pytest.mark.django_db
def test_dispute_endpoint_rejects_get_request(pytest_client):
    response = pytest_client.get(
        reverse(
            "client-invoice-dispute",
            kwargs={"token": "some-token"},
        )
    )

    assert response.status_code == 405

@pytest.mark.django_db
def test_invoice_specific_payment_url_is_displayed(
    make_invoice, pytest_client, settings
):
    settings.PAYMENT_URL = "https://default-payment.example.com"

    invoice = make_invoice(
        due_date=datetime.date(2026, 3, 10),
    )
    invoice.status = "sent"
    invoice.payment_url = "https://invoice-payment.example.com"
    invoice.save(update_fields=["status", "payment_url"])

    token = generate_invoice_token(invoice.pk)

    response = pytest_client.get(
        reverse("client-invoice", kwargs={"token": token})
    )

    assert response.status_code == 200
    assert b"https://invoice-payment.example.com" in response.content
    assert b"https://default-payment.example.com" not in response.content

@pytest.mark.django_db
def test_default_payment_url_is_displayed(
    make_invoice, pytest_client, settings
):
    settings.PAYMENT_URL = "https://default-payment.example.com"

    invoice = make_invoice(
        due_date=datetime.date(2026, 3, 10),
    )
    invoice.status = "sent"
    invoice.save(update_fields=["status"])

    token = generate_invoice_token(invoice.pk)

    response = pytest_client.get(
        reverse("client-invoice", kwargs={"token": token})
    )

    assert response.status_code == 200
    assert b"https://default-payment.example.com" in response.content
