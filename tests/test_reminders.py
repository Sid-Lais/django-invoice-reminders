import base64
import datetime
from io import StringIO
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from invoices.models import Client, Invoice, ReminderLog
from invoices.services.email import FakeMailer
from invoices.services.invoicing import mark_paid
from invoices.services.reminders import run_reminders

from .conftest import categories

TODAY = datetime.date(2026, 3, 10)
REMINDER_CATEGORIES = {"reminder-heads-up", "reminder-due-today", "reminder-overdue", "reminder-final"}


def days(n: int) -> datetime.timedelta:
    return datetime.timedelta(days=n)


@pytest.fixture
def four_invoices(make_invoice):
    return [make_invoice(due_date=TODAY + days(offset)) for offset in (3, 0, -7, -14)]


def run_command(*args) -> str:
    out = StringIO()
    call_command("send_payment_reminders", *args, stdout=out)
    return out.getvalue()


# --- Acceptance ----------------------------------------------------------------


def test_running_twice_on_same_day_sends_each_due_reminder_exactly_once(four_invoices, outbox):
    first = run_command("--date", TODAY.isoformat())
    assert "sent=4" in first
    assert sorted(categories(outbox)) == sorted(REMINDER_CATEGORIES)

    second = run_command("--date", TODAY.isoformat())
    assert "sent=0" in second
    assert len(outbox) == 4
    assert ReminderLog.objects.filter(status="sent").count() == 4


def test_marking_paid_stops_all_future_reminders(make_invoice, outbox):
    invoice = make_invoice(due_date=TODAY)
    run_reminders(TODAY - days(3))
    assert categories(outbox) == ["reminder-heads-up"]

    mark_paid(invoice)
    assert categories(outbox) == ["reminder-heads-up", "payment-receipt"]

    for offset in range(-2, 40):
        run_reminders(TODAY + days(offset))
    assert categories(outbox) == ["reminder-heads-up", "payment-receipt"]
    assert ReminderLog.objects.filter(invoice=invoice).count() == 1


# --- Which invoices are considered -----------------------------------------------


@pytest.mark.parametrize("status", ["draft", "void", "paid"])
def test_non_sent_invoices_never_get_reminders(make_invoice, outbox, status):
    make_invoice(due_date=TODAY, status=status)
    for offset in range(-5, 30):
        run_reminders(TODAY + days(offset))
    assert outbox == []
    assert not ReminderLog.objects.exists()


@pytest.mark.parametrize("email_status", ["bounced", "spam"])
def test_flagged_clients_are_skipped_and_nothing_recorded(make_invoice, outbox, email_status):
    flagged = Client.objects.create(name="Bad", email="bad@example.com", email_status=email_status)
    make_invoice(due_date=TODAY, client=flagged)
    summary = run_reminders(TODAY)
    assert outbox == []
    assert not ReminderLog.objects.exists()
    assert summary.skipped == 1 and summary.outcomes[0].action == "flagged"


def test_missed_stages_recorded_as_skipped(make_invoice, outbox):
    invoice = make_invoice(due_date=TODAY - days(14))
    run_reminders(TODAY)
    logs = {log.stage: log.status for log in invoice.reminder_logs.all()}
    assert logs == {"heads_up": "skipped", "due_today": "skipped", "overdue": "skipped", "final_notice": "sent"}
    assert categories(outbox) == ["reminder-final"]


def test_no_reminder_on_same_local_day_invoice_was_sent(make_invoice, outbox):
    make_invoice(due_date=TODAY, sent_on=TODAY)
    run_reminders(TODAY)
    run_reminders(TODAY + days(1))
    assert outbox == []
    run_reminders(TODAY + days(7))
    assert categories(outbox) == ["reminder-overdue"]


# --- Failures & retries ----------------------------------------------------------


def test_failed_send_is_retried_next_run_with_a_single_row(make_invoice, outbox):
    invoice = make_invoice(due_date=TODAY)
    ReminderLog.objects.create(invoice=invoice, stage="heads_up", status="sent", scheduled_for=TODAY - days(3))

    FakeMailer.fail_with = "Mailtrap API error (500): boom"
    with pytest.raises(CommandError, match="1 reminder"):
        run_command("--date", TODAY.isoformat())
    log = ReminderLog.objects.get(invoice=invoice, stage="due_today")
    assert log.status == "failed" and "boom" in log.error

    FakeMailer.fail_with = None
    output = run_command("--date", (TODAY + days(1)).isoformat())
    assert "sent=1" in output
    log.refresh_from_db()
    assert log.status == "sent" and log.message_id.startswith("fake-") and log.error == ""
    assert ReminderLog.objects.filter(invoice=invoice, stage="due_today").count() == 1
    assert categories(outbox) == ["reminder-due-today"]


def test_stage_already_claimed_is_not_sent_again(make_invoice, outbox):
    invoice = make_invoice(due_date=TODAY)
    # Another runner claimed the stage but hasn't finished (or crashed).
    ReminderLog.objects.create(invoice=invoice, stage="heads_up", status="pending", scheduled_for=TODAY - days(3))
    run_reminders(TODAY - days(3))
    assert outbox == []


def test_dry_run_writes_nothing_and_sends_nothing(four_invoices, outbox):
    output = run_command("--dry-run", "--date", TODAY.isoformat())
    assert "DRY RUN" in output and "would send=4" in output
    assert output.count("WOULD-SEND") == 4
    assert outbox == []
    assert not ReminderLog.objects.exists()


def test_invalid_date_argument():
    with pytest.raises(CommandError, match="YYYY-MM-DD"):
        run_command("--date", "10/03/2026")


# --- Time zone ---------------------------------------------------------------------


def test_today_uses_local_time_zone_around_midnight(settings, make_invoice, outbox):
    assert settings.TIME_ZONE == "Asia/Kolkata"
    invoice = make_invoice(due_date=TODAY)
    ReminderLog.objects.create(invoice=invoice, stage="heads_up", status="sent", scheduled_for=TODAY - days(3))

    # 18:29 UTC on the day before = 23:59 IST, still the day before the due date.
    before = datetime.datetime(2026, 3, 9, 18, 29, tzinfo=datetime.UTC)
    with mock.patch("django.utils.timezone.now", return_value=before):
        assert timezone.localdate() == TODAY - days(1)
        run_command()
    assert outbox == []

    # 18:31 UTC = 00:01 IST on the due date (UTC date is still the 9th).
    after = datetime.datetime(2026, 3, 9, 18, 31, tzinfo=datetime.UTC)
    with mock.patch("django.utils.timezone.now", return_value=after):
        assert timezone.localdate() == TODAY
        run_command()
    assert categories(outbox) == ["reminder-due-today"]


# --- Payload ------------------------------------------------------------------------


def test_reminder_payload_has_pdf_attachment_and_stage_category(four_invoices, outbox):
    run_reminders(TODAY)
    numbers = {invoice.number for invoice in four_invoices}
    for payload in outbox:
        assert payload["category"] in REMINDER_CATEGORIES
        assert payload["text"] and payload["html"]
        assert payload["to"] == [{"email": "ananya@example.com", "name": "Ananya Rao"}]
        (attachment,) = payload["attachments"]
        number = attachment["filename"].removeprefix("Invoice-").removesuffix(".pdf")
        assert number in numbers
        assert attachment["type"] == "application/pdf"
        assert attachment["disposition"] == "attachment"
        assert base64.b64decode(attachment["content"]).startswith(b"%PDF")
        assert number in payload["subject"]


def test_overdue_subject_and_body_mention_days_overdue(make_invoice, outbox):
    make_invoice(due_date=TODAY - days(7))
    run_reminders(TODAY)
    (payload,) = outbox
    assert "7 days overdue" in payload["subject"]
    assert "7 days overdue" in payload["text"]


# --- seed_demo ----------------------------------------------------------------------


def test_seed_demo_creates_four_invoices_at_each_stage_and_is_idempotent(db, outbox):
    call_command("seed_demo", "--email", "Me@Example.org", stdout=StringIO())
    call_command("seed_demo", "--email", "me@example.org", stdout=StringIO())

    assert Client.objects.count() == 1
    invoices = Invoice.objects.filter(status="sent").order_by("number")
    assert invoices.count() == 4
    today = timezone.localdate()
    assert sorted((inv.due_date - today).days for inv in invoices) == [-14, -7, 0, 3]
    assert all(inv.line_items.exists() and inv.total > 0 for inv in invoices)

    summary = run_reminders()
    assert summary.sent == 4
    assert sorted(categories(outbox)) == sorted(REMINDER_CATEGORIES)
    assert run_reminders().sent == 0


def test_seed_demo_reset_recreates_invoices(db):
    call_command("seed_demo", stdout=StringIO())
    run_reminders()
    assert ReminderLog.objects.exists()
    call_command("seed_demo", "--reset", stdout=StringIO())
    assert Invoice.objects.count() == 4
    assert not ReminderLog.objects.exists()
