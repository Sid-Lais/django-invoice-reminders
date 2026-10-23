"""Payment-reminder sequence.

Two layers:

* `plan_stages()` is a pure function: given the due date, the local date the
  invoice was sent, "today" and which stages are already logged, it decides
  which single stage (if any) to send and which missed stages to skip.
* `run_reminders()` applies that plan to every sent invoice, claiming each
  stage with a ReminderLog row *before* sending so a stage is never sent twice.

"Today" is the *client's* local date (Client.time_zone), never the UTC date,
and reminders are only sent while the client's local time is inside
settings.REMINDER_SEND_HOURS. Passing an explicit `today` (the --date flag)
uses that date for every client and ignores the send window.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import Invoice, ReminderLog
from .email import Mailer, get_mailer
from .invoicing import build_message, email_context

logger = logging.getLogger(__name__)

RETRYABLE = {ReminderLog.Status.FAILED}


@dataclass(frozen=True)
class Stage:
    key: str
    offset_days: int
    category: str
    subject: str
    template: str

    def date_for(self, due_date: datetime.date) -> datetime.date:
        return due_date + datetime.timedelta(days=self.offset_days)


def get_stages(config: list[dict] | None = None) -> list[Stage]:
    config = settings.REMINDER_STAGES if config is None else config
    stages = [Stage(**item) for item in config]
    keys = [stage.key for stage in stages]
    if len(keys) != len(set(keys)):
        raise ValueError("REMINDER_STAGES keys must be unique.")
    return sorted(stages, key=lambda stage: stage.offset_days)


@dataclass
class StagePlan:
    send: Stage | None = None
    skip: list[Stage] = field(default_factory=list)


def plan_stages(
    *,
    due_date: datetime.date,
    sent_date: datetime.date | None,
    today: datetime.date,
    stages: list[Stage],
    logged: dict[str, str],
) -> StagePlan:
    """Decide what to do for one invoice today.

    - Only stages dated on/before `today` with no log row (or a failed one,
      which is retried) are candidates.
    - Stages dated on/before the day the invoice was emailed are skipped:
      the invoice email itself covers them, and nothing is ever sent on the
      same local date as the invoice.
    - Of the rest, only the latest is sent; earlier missed ones are skipped,
      so a client never receives a burst of reminders.
    """
    candidates = [
        stage
        for stage in sorted(stages, key=lambda s: s.offset_days)
        if stage.date_for(due_date) <= today
        and (stage.key not in logged or logged[stage.key] in RETRYABLE)
    ]
    covered: list[Stage] = []
    if sent_date is not None:
        covered = [s for s in candidates if s.date_for(due_date) <= sent_date]
        candidates = [s for s in candidates if s.date_for(due_date) > sent_date]
    if not candidates:
        return StagePlan(send=None, skip=covered)
    return StagePlan(send=candidates[-1], skip=covered + candidates[:-1])


@dataclass
class InvoiceOutcome:
    invoice: str
    email: str
    action: str  # sent | failed | flagged | nothing | would-send | waiting
    stage: str = ""
    skipped: list[str] = field(default_factory=list)
    detail: str = ""

    def describe(self) -> str:
        parts = [f"{self.invoice:<16} {self.email:<32} {self.action.upper()}"]
        if self.stage:
            parts.append(self.stage)
        if self.skipped:
            parts.append(f"(skipped: {', '.join(self.skipped)})")
        if self.detail:
            parts.append(f"- {self.detail}")
        return " ".join(parts)


@dataclass
class RunSummary:
    today: datetime.date | None  # None = each client's own local date
    now: datetime.datetime
    dry_run: bool = False
    checked: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    waiting: int = 0
    outcomes: list[InvoiceOutcome] = field(default_factory=list)


def in_send_window(local_time: datetime.datetime, window: tuple[int, int] | None = None) -> bool:
    start, end = window or settings.REMINDER_SEND_HOURS
    return start <= local_time.hour < end


def build_reminder_message(invoice: Invoice, stage: Stage, today: datetime.date):
    days_overdue = invoice.days_overdue_on(today)
    subject = stage.subject.format(
        number=invoice.number,
        due_date=invoice.due_date.strftime("%d %b %Y"),
        total=invoice.total_display,
        business_name=settings.BUSINESS_NAME,
        days_overdue=days_overdue,
    )
    context = email_context(
        invoice,
        stage=stage,
        today=today,
        days_overdue=days_overdue,
        days_until_due=(invoice.due_date - today).days,
    )
    return build_message(
        invoice, template=stage.template, subject=subject, category=stage.category, context=context
    )


def _mark_skipped(invoice: Invoice, stage: Stage, reason: str) -> None:
    log, created = ReminderLog.objects.get_or_create(
        invoice=invoice,
        stage=stage.key,
        defaults={
            "status": ReminderLog.Status.SKIPPED,
            "scheduled_for": stage.date_for(invoice.due_date),
            "error": reason,
        },
    )
    if not created and log.status in RETRYABLE:
        ReminderLog.objects.filter(pk=log.pk, status=log.status).update(
            status=ReminderLog.Status.SKIPPED, error=f"{reason} (previous error: {log.error})"
        )


def _claim(invoice: Invoice, stage: Stage) -> ReminderLog | None:
    """Atomically take ownership of (invoice, stage). None = someone else has it."""
    existing = ReminderLog.objects.filter(invoice=invoice, stage=stage.key).first()
    if existing is None:
        try:
            with transaction.atomic():
                return ReminderLog.objects.create(
                    invoice=invoice,
                    stage=stage.key,
                    status=ReminderLog.Status.PENDING,
                    scheduled_for=stage.date_for(invoice.due_date),
                )
        except IntegrityError:
            return None
    if existing.status in RETRYABLE:
        # Conditional UPDATE: only one concurrent runner can flip failed -> pending.
        claimed = ReminderLog.objects.filter(pk=existing.pk, status=existing.status).update(
            status=ReminderLog.Status.PENDING
        )
        if claimed:
            existing.refresh_from_db()
            return existing
    return None


def process_invoice(
    invoice: Invoice,
    *,
    today: datetime.date,
    stages: list[Stage],
    summary: RunSummary,
    mailer_factory,
    local_time: datetime.datetime | None = None,
) -> InvoiceOutcome:
    """Process one invoice. `today` is the client's local date; `local_time`,
    when given, is the client's local time and enforces the send window."""
    outcome = InvoiceOutcome(invoice=invoice.number, email=invoice.client.email, action="nothing")

    if invoice.client.is_email_flagged:
        logger.warning(
            "Skipping %s: client email %s is flagged (%s)",
            invoice.number,
            invoice.client.email,
            invoice.client.email_status,
        )
        outcome.action = "flagged"
        outcome.detail = f"client email is {invoice.client.email_status}"
        summary.skipped += 1
        return outcome

    logged = {log.stage: log.status for log in invoice.reminder_logs.all()}
    sent_date = invoice.client.local_date(invoice.sent_at) if invoice.sent_at else None
    plan = plan_stages(
        due_date=invoice.due_date, sent_date=sent_date, today=today, stages=stages, logged=logged
    )

    if plan.send and local_time is not None and not in_send_window(local_time):
        # Due, but it's outside working hours where the client is: a later
        # (hourly) run picks it up. Nothing is written yet.
        start, end = settings.REMINDER_SEND_HOURS
        outcome.action, outcome.stage = "waiting", plan.send.key
        outcome.detail = (
            f"{local_time:%H:%M} in {invoice.client.time_zone}; sends between {start:02d}:00 and {end:02d}:00"
        )
        summary.waiting += 1
        return outcome

    outcome.skipped = [stage.key for stage in plan.skip]
    summary.skipped += len(plan.skip)

    if summary.dry_run:
        if plan.send:
            outcome.action, outcome.stage = "would-send", plan.send.key
        return outcome

    for stage in plan.skip:
        reason = (
            "Covered by the invoice email."
            if sent_date and stage.date_for(invoice.due_date) <= sent_date
            else f"Missed; superseded by a later stage on {today.isoformat()}."
        )
        _mark_skipped(invoice, stage, reason)

    if plan.send is None:
        return outcome

    stage = plan.send
    outcome.stage = stage.key
    log = _claim(invoice, stage)
    if log is None:
        outcome.detail = "already claimed by another run"
        return outcome

    # The invoice may have been paid/voided since the queryset was evaluated.
    if not Invoice.objects.filter(pk=invoice.pk, status=Invoice.Status.SENT).exists():
        log.status = ReminderLog.Status.SKIPPED
        log.error = "Invoice is no longer in sent status."
        log.save(update_fields=["status", "error"])
        outcome.detail = log.error
        return outcome

    try:
        result = mailer_factory().send(build_reminder_message(invoice, stage, today))
    except Exception as exc:  # any failure must leave a retryable row, never "pending"
        log.status = ReminderLog.Status.FAILED
        log.error = str(exc) or type(exc).__name__
        log.save(update_fields=["status", "error"])
        logger.error("Reminder %s for %s failed: %s", stage.key, invoice.number, log.error)
        summary.failed += 1
        outcome.action, outcome.detail = "failed", log.error
        return outcome

    log.status = ReminderLog.Status.SENT
    log.sent_at = timezone.now()
    log.message_id = result.message_id
    log.error = ""
    log.save(update_fields=["status", "sent_at", "message_id", "error"])
    summary.sent += 1
    outcome.action = "sent"
    return outcome


def run_reminders(
    today: datetime.date | None = None,
    *,
    dry_run: bool = False,
    mailer: Mailer | None = None,
    stages: list[Stage] | None = None,
    now: datetime.datetime | None = None,
) -> RunSummary:
    """Run the reminder sequence for every sent invoice.

    With `today=None` (normal use), each client's local date and send window
    are used. An explicit `today` applies to every client and skips the window.
    """
    now = now or timezone.now()
    stages = stages if stages is not None else get_stages()
    summary = RunSummary(today=today, now=now, dry_run=dry_run)

    cached: list[Mailer] = [mailer] if mailer else []

    def mailer_factory() -> Mailer:
        if not cached:
            cached.append(get_mailer())
        return cached[0]

    invoices = (
        Invoice.objects.filter(status=Invoice.Status.SENT)
        .select_related("client")
        .prefetch_related("line_items", "reminder_logs")
        .order_by("due_date", "id")
    )
    for invoice in invoices:
        summary.checked += 1
        if today is not None:
            local_today, local_time = today, None
        else:
            local_time = invoice.client.local_now(now)
            local_today = local_time.date()
        summary.outcomes.append(
            process_invoice(
                invoice,
                today=local_today,
                local_time=local_time,
                stages=stages,
                summary=summary,
                mailer_factory=mailer_factory,
            )
        )
    return summary
