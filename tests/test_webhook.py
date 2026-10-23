import hashlib
import hmac
import json

import pytest
from django.urls import reverse

from invoices.models import Client, WebhookEvent

URL = reverse("mailtrap-webhook", args=["test-webhook-token"])


@pytest.fixture
def customer(db):
    return Client.objects.create(name="Ravi", email="Ravi.Kumar@Example.com")


def post(http, payload, url=URL, **headers):
    body = payload if isinstance(payload, (str, bytes)) else json.dumps(payload)
    return http.post(url, data=body, content_type="application/json", headers=headers)


def event(kind, email="ravi.kumar@example.com", event_id="evt-1", **extra):
    return {
        "event": kind,
        "email": email,
        "event_id": event_id,
        "message_id": "msg-1",
        "category": "invoice",
        "timestamp": 1773100000,
        **extra,
    }


def test_bounce_flags_client_case_insensitively(client, customer):
    response = post(client, {"events": [event("bounce", email="RAVI.KUMAR@example.com", reason="mailbox does not exist")]})
    assert response.status_code == 200
    customer.refresh_from_db()
    assert customer.email_status == "bounced"
    assert customer.email_flagged_at is not None
    assert "bounce" in customer.email_flag_reason and "mailbox does not exist" in customer.email_flag_reason


def test_spam_flags_client(client, customer):
    post(client, {"events": [event("spam")]})
    customer.refresh_from_db()
    assert customer.email_status == "spam" and customer.email_flagged_at is not None


@pytest.mark.parametrize("kind", ["delivery", "open", "click", "soft bounce", "unsubscribe"])
def test_other_events_are_stored_but_do_not_flag(client, customer, kind):
    assert post(client, {"events": [event(kind)]}).status_code == 200
    customer.refresh_from_db()
    assert customer.email_status == "ok"
    assert WebhookEvent.objects.filter(event_type=kind).count() == 1


def test_duplicate_event_id_processed_once(client, customer):
    payload = {"events": [event("bounce", event_id="dup-1")]}
    first = post(client, payload)
    assert first.json()["processed"] == 1
    customer.clear_email_flag()

    second = post(client, payload)
    assert second.status_code == 200
    assert second.json() == {"ok": True, "received": 1, "processed": 0, "duplicates": 1}
    customer.refresh_from_db()
    assert customer.email_status == "ok"  # not re-flagged
    assert WebhookEvent.objects.count() == 1


def test_multiple_events_in_one_request(client, customer):
    payload = {"events": [event("delivery", event_id="a"), event("open", event_id="b"), event("spam", event_id="c")]}
    assert post(client, payload).json()["processed"] == 3
    customer.refresh_from_db()
    assert customer.email_status == "spam"


def test_wrong_token_404(client, customer):
    response = post(client, {"events": [event("bounce")]}, url=reverse("mailtrap-webhook", args=["wrong"]))
    assert response.status_code == 404
    assert not WebhookEvent.objects.exists()


def test_get_not_allowed(client, db):
    assert client.get(URL).status_code == 405


@pytest.mark.parametrize("body", ["{not json", '"just a string"', '{"events": "nope"}', "[]"])
def test_malformed_json_400(client, db, body):
    assert post(client, body).status_code == 400


def test_unknown_email_ignored(client, customer):
    response = post(client, {"events": [event("bounce", email="stranger@example.com")]})
    assert response.status_code == 200
    customer.refresh_from_db()
    assert customer.email_status == "ok"
    assert WebhookEvent.objects.count() == 1


def test_signature_verified_when_signing_secret_set(client, customer, settings):
    settings.MAILTRAP_WEBHOOK_SIGNING_SECRET = "whsec"
    body = json.dumps({"events": [event("bounce")]})
    assert post(client, body).status_code == 401
    assert post(client, body, **{"Mailtrap-Signature": "0" * 64}).status_code == 401

    signature = hmac.new(b"whsec", body.encode(), hashlib.sha256).hexdigest()
    assert post(client, body, **{"Mailtrap-Signature": signature}).status_code == 200
    customer.refresh_from_db()
    assert customer.email_status == "bounced"
