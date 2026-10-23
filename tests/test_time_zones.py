"""Each client is reminded on their own local date, inside their working hours."""

import datetime
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.urls import reverse

from invoices.models import Client, Invoice, ReminderLog
from invoices.services.reminders import run_reminders

from .conftest import categories

DUE = datetime.date(2026, 3, 10)
UTC = datetime.UTC


def at(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    """A UTC instant in March 2026."""
    return datetime.datetime(2026, 3, day, hour, minute, tzinfo=UTC)


def client_in(tz: str, email: str) -> Client:
    return Client.objects.create(name=tz, email=email, time_zone=tz)


def heads_up_done(invoice: Invoice) -> None:
    ReminderLog.objects.create(
        invoice=invoice, stage="heads_up", status="sent", scheduled_for=DUE - datetime.timedelta(days=3)
    )


@pytest.fixture
def work_hours(settings):
    settings.REMINDER_SEND_HOURS = (9, 18)


def test_client_defaults_to_business_time_zone(db, settings):
    client = Client.objects.create(name="A", email="a@example.com")
    assert client.time_zone == settings.TIME_ZONE == "Asia/Kolkata"


def test_invalid_time_zone_rejected(db):
    client = Client(name="A", email="a@example.com", time_zone="Mars/Olympus")
    with pytest.raises(ValidationError, match="Unknown time zone"):
        client.full_clean()


def test_each_client_gets_due_today_on_their_own_date(db, make_invoice, outbox, work_hours):
    tokyo = make_invoice(due_date=DUE, client=client_in("Asia/Tokyo", "tokyo@example.com"))
    new_york = make_invoice(due_date=DUE, client=client_in("America/New_York", "ny@example.com"))
    for invoice in (tokyo, new_york):
        heads_up_done(invoice)

    # 00:30 UTC on 10 Mar: 09:30 on 10 Mar in Tokyo, 20:30 on 9 Mar in New York.
    run_reminders(now=at(10, 0, 30))
    assert [p["to"][0]["email"] for p in outbox] == ["tokyo@example.com"]

    # 13:30 UTC: 09:30 on 10 Mar in New York (EDT, UTC-4). Tokyo was already reminded.
    run_reminders(now=at(10, 13, 30))
    assert [p["to"][0]["email"] for p in outbox] == ["tokyo@example.com", "ny@example.com"]
    assert categories(outbox) == ["reminder-due-today", "reminder-due-today"]


def test_hourly_runs_send_once_at_start_of_working_hours(db, make_invoice, outbox, work_hours):
    invoice = make_invoice(due_date=DUE, client=client_in("Europe/Berlin", "berlin@example.com"))
    heads_up_done(invoice)

    sent_at_utc_hour = []
    for hour in range(24):  # an hourly cron across 10 March (UTC)
        before = len(outbox)
        run_reminders(now=at(10, hour))
        if len(outbox) > before:
            sent_at_utc_hour.append(hour)
    # Berlin is UTC+1 in March: 08:00 UTC = 09:00 local, the first hour in the window.
    assert sent_at_utc_hour == [8]
    assert ReminderLog.objects.filter(invoice=invoice, stage="due_today").count() == 1


def test_outside_working_hours_waits_and_writes_nothing(db, make_invoice, outbox, work_hours):
    invoice = make_invoice(due_date=DUE)  # client in Asia/Kolkata (UTC+5:30)
    heads_up_done(invoice)

    for early_or_late in (at(10, 2, 0), at(10, 12, 45)):  # 07:30 and 18:15 IST
        summary = run_reminders(now=early_or_late)
        assert summary.waiting == 1 and summary.sent == 0
        assert summary.outcomes[0].action == "waiting"
        assert "Asia/Kolkata" in summary.outcomes[0].detail
    assert outbox == []
    assert ReminderLog.objects.filter(invoice=invoice).count() == 1  # only the heads_up we added

    assert run_reminders(now=at(10, 3, 45)).sent == 1  # 09:15 IST


def test_half_hour_time_zone_is_reached_by_hourly_runs(db, make_invoice, outbox, work_hours):
    invoice = make_invoice(due_date=DUE, client=client_in("Asia/Kathmandu", "ktm@example.com"))  # UTC+5:45
    heads_up_done(invoice)
    for hour in range(24):
        run_reminders(now=at(10, hour))
    assert categories(outbox) == ["reminder-due-today"]


def test_same_day_as_sent_uses_client_local_date(db, make_invoice, outbox):
    # Sent at 23:00 on 9 Mar in Los Angeles (PDT, UTC-7) = 06:00 UTC on 10 Mar, already the due date in UTC.
    la = client_in("America/Los_Angeles", "la@example.com")
    invoice = make_invoice(due_date=DUE, client=la)
    invoice.sent_at = at(10, 6, 0)
    invoice.save()
    # 17:00 UTC on 10 Mar = 10:00 on 10 Mar in LA: a day after it was sent there.
    run_reminders(now=at(10, 17, 0))
    assert categories(outbox) == ["reminder-due-today"]


def test_date_override_applies_to_everyone_and_ignores_window(db, make_invoice, outbox, work_hours):
    make_invoice(due_date=DUE, client=client_in("Pacific/Auckland", "akl@example.com"))
    make_invoice(due_date=DUE, client=client_in("America/Chicago", "chi@example.com"))
    out = StringIO()
    call_command("send_payment_reminders", "--date", DUE.isoformat(), stdout=out)
    assert "all clients, send window ignored" in out.getvalue()
    assert len(outbox) == 2


def test_command_reports_waiting(db, make_invoice, outbox, work_hours, monkeypatch):
    invoice = make_invoice(due_date=DUE)
    heads_up_done(invoice)
    monkeypatch.setattr("django.utils.timezone.now", lambda: at(10, 20, 0))  # 01:30 IST
    out = StringIO()
    call_command("send_payment_reminders", stdout=out)
    text = out.getvalue()
    assert "WAITING due_today" in text and "waiting=1" in text and "sent=0" in text


def test_days_overdue_uses_client_date(db, make_invoice, monkeypatch):
    invoice = make_invoice(due_date=DUE, client=client_in("Pacific/Kiritimati", "kir@example.com"))  # UTC+14
    monkeypatch.setattr("django.utils.timezone.now", lambda: at(10, 11, 0))  # already 11 Mar there
    assert invoice.days_overdue == 1


def test_seed_demo_time_zone_option(db):
    call_command("seed_demo", "--email", "me@example.org", "--time-zone", "America/New_York", stdout=StringIO())
    client = Client.objects.get(email="me@example.org")
    assert client.time_zone == "America/New_York"
    with pytest.raises(CommandError, match="Unknown time zone"):
        call_command("seed_demo", "--time-zone", "Nowhere/Land", stdout=StringIO())


def test_admin_client_form_offers_time_zones(admin_client):
    page = admin_client.get(reverse("admin:invoices_client_add"))
    assert b"America/New_York" in page.content and b"Asia/Tokyo" in page.content
    response = admin_client.post(
        reverse("admin:invoices_client_add"),
        {"name": "Zoe", "company": "", "email": "zoe@example.com", "email_status": "ok",
         "email_flag_reason": "", "time_zone": "Europe/Paris"},
    )
    assert response.status_code == 302
    assert Client.objects.get(email="zoe@example.com").time_zone == "Europe/Paris"
    listing = admin_client.get(reverse("admin:invoices_client_changelist"))
    assert b"Europe/Paris" in listing.content
