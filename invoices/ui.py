"""Dashboard: create invoices, send them, and track reminders."""

from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import InvoiceForm
from .models import Invoice, ReminderLog
from .services.email import EmailSendError
from .services.invoicing import InvoiceStateError, mark_paid, resend_invoice, send_invoice, void_invoice
from .services.reminders import get_stages

STAGE_LABELS = {
    "heads_up": "Heads-up",
    "due_today": "Due today",
    "overdue": "Overdue",
    "final_notice": "Final notice",
}


def stage_label(key: str) -> str:
    return STAGE_LABELS.get(key, key.replace("_", " ").capitalize())


def _when(offset_days: int) -> str:
    if offset_days == 0:
        return "On the due date"
    days = abs(offset_days)
    unit = "day" if days == 1 else "days"
    return f"{days} {unit} {'before' if offset_days < 0 else 'after'} the due date"


def _due_note(invoice: Invoice, today: datetime.date) -> str:
    if invoice.status == Invoice.Status.PAID and invoice.paid_at:
        return f"Paid {invoice.client.local_date(invoice.paid_at):%-d %b}"
    if invoice.status != Invoice.Status.SENT:
        return ""
    delta = (invoice.due_date - today).days
    if delta == 0:
        return "Due today"
    unit = "day" if abs(delta) == 1 else "days"
    return f"in {delta} {unit}" if delta > 0 else f"{-delta} {unit} ago"


def _row(invoice: Invoice, stages) -> dict:
    today = invoice.client.local_date()
    items = list(invoice.line_items.all())
    logs = {log.stage: log for log in invoice.reminder_logs.all()}
    overdue = invoice.days_overdue_on(today)
    if invoice.status == Invoice.Status.SENT and overdue:
        key, label = "overdue", f"Overdue {overdue}d"
    else:
        key, label = invoice.status, invoice.get_status_display()

    next_reminder = None
    if invoice.status == Invoice.Status.SENT:
        handled = {k for k, log in logs.items() if log.status != ReminderLog.Status.FAILED}
        sent_on = invoice.client.local_date(invoice.sent_at) if invoice.sent_at else today
        for stage in stages:
            when = stage.date_for(invoice.due_date)
            if stage.key not in handled and when > sent_on:
                next_reminder = {"key": stage.key, "label": stage_label(stage.key), "date": max(when, today)}
                break

    steps = []
    for stage in stages:
        log = logs.get(stage.key)
        if log and log.status == ReminderLog.Status.SENT:
            state = "sent"
        elif log and log.status == ReminderLog.Status.FAILED:
            state = "failed"
        elif log and log.status == ReminderLog.Status.SKIPPED:
            state = "skipped"
        elif next_reminder and next_reminder["key"] == stage.key:
            state = "next"
        else:
            state = "pending"
        steps.append({"label": stage_label(stage.key), "state": state})

    groups = ["all"]
    if key in ("sent", "overdue"):
        groups.append("outstanding")
    if key in ("overdue", "paid", "draft"):
        groups.append(key)

    return {
        "invoice": invoice,
        "name": items[0].description if items else "",
        "status_key": key,
        "status_label": label,
        "due_note": _due_note(invoice, today),
        "steps": steps,
        "steps_summary": ", ".join(f"{step['label']} {step['state']}" for step in steps),
        "next_reminder": next_reminder,
        "groups": " ".join(groups),
    }


def dashboard(request, form: InvoiceForm | None = None):
    stages = get_stages()
    invoices = list(
        Invoice.objects.select_related("client").prefetch_related("line_items", "reminder_logs")
    )
    rows = [_row(invoice, stages) for invoice in invoices]

    outstanding: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        if row["invoice"].status == Invoice.Status.SENT:
            outstanding[row["invoice"].currency] += row["invoice"].total
    tabs = [
        {"key": key, "label": label, "count": sum(1 for row in rows if key in row["groups"].split())}
        for key, label in (("all", "All"), ("outstanding", "Outstanding"), ("overdue", "Overdue"), ("paid", "Paid"), ("draft", "Drafts"))
    ]
    stats = {
        "outstanding": [f"{code} {amount:,.2f}" for code, amount in sorted(outstanding.items())],
        "overdue": sum(1 for row in rows if row["status_key"] == "overdue"),
        "paid": sum(1 for row in rows if row["status_key"] == "paid"),
        "total": len(rows),
    }
    return render(
        request,
        "app/dashboard.html",
        {
            "form": form or InvoiceForm(initial={"due_date": (datetime.date.today() + datetime.timedelta(days=14)).isoformat()}),
            "rows": rows,
            "stats": stats,
            "tabs": tabs,
            "schedule": [{"label": stage_label(s.key), "when": _when(s.offset_days)} for s in stages],
        },
    )


@require_POST
def create_invoice(request):
    form = InvoiceForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please fix the highlighted fields.")
        return dashboard(request, form=form)

    invoice = form.save()
    if form.cleaned_data["send_now"]:
        try:
            send_invoice(invoice)
        except EmailSendError as exc:
            messages.warning(
                request, f"{invoice.number} was saved as a draft, but the email failed: {exc}"
            )
        else:
            messages.success(
                request, f"{invoice.number} saved and emailed to {invoice.client.email}. Reminders will follow automatically."
            )
    else:
        messages.success(request, f"{invoice.number} saved as a draft.")
    return redirect("dashboard")


ACTIONS = {
    "send": lambda invoice: (send_invoice(invoice), "sent to"),
    "resend": lambda invoice: (resend_invoice(invoice), "sent again to"),
    "paid": lambda invoice: (mark_paid(invoice), "marked paid; a receipt went to"),
    "void": lambda invoice: (void_invoice(invoice), "voided for"),
}


@require_POST
def invoice_action(request, pk: int, action: str):
    invoice = get_object_or_404(Invoice.objects.select_related("client"), pk=pk)
    if action not in ACTIONS:
        return redirect("dashboard")
    try:
        _, phrase = ACTIONS[action](invoice)
    except InvoiceStateError as exc:
        messages.error(request, str(exc))
    except EmailSendError as exc:
        messages.error(request, f"{invoice.number}: the email failed: {exc}")
    else:
        messages.success(request, f"{invoice.number} {phrase} {invoice.client.email}.")
    return redirect("dashboard")
