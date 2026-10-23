"""Pure stage-calculation tests: no database, explicit `today`."""

import datetime

import pytest

from invoices.services.reminders import get_stages, plan_stages

DUE = datetime.date(2026, 3, 10)
SENT = DUE - datetime.timedelta(days=30)
STAGES = get_stages()


def plan(today, logged=None, sent=SENT):
    result = plan_stages(due_date=DUE, sent_date=sent, today=today, stages=STAGES, logged=logged or {})
    return (result.send.key if result.send else None), [s.key for s in result.skip]


def day(offset: int) -> datetime.date:
    return DUE + datetime.timedelta(days=offset)


def test_default_stages_are_ordered_with_expected_offsets():
    assert [(s.key, s.offset_days, s.category) for s in STAGES] == [
        ("heads_up", -3, "reminder-heads-up"),
        ("due_today", 0, "reminder-due-today"),
        ("overdue", 7, "reminder-overdue"),
        ("final_notice", 14, "reminder-final"),
    ]


def test_nothing_before_first_stage():
    assert plan(day(-4)) == (None, [])


@pytest.mark.parametrize(
    "offset, logged, expected",
    [
        (-3, {}, "heads_up"),
        (0, {"heads_up": "sent"}, "due_today"),
        (7, {"heads_up": "sent", "due_today": "sent"}, "overdue"),
        (14, {"heads_up": "sent", "due_today": "sent", "overdue": "sent"}, "final_notice"),
    ],
)
def test_each_stage_fires_on_its_offset(offset, logged, expected):
    assert plan(day(offset), logged) == (expected, [])


@pytest.mark.parametrize("offset", [-2, -1, 1, 6, 8, 13, 15, 60])
def test_days_between_stages_send_nothing_once_caught_up(offset):
    logged = {key: "sent" for key in ("heads_up", "due_today", "overdue", "final_notice")}
    # Only stages dated on/before today could be logged; extras are harmless.
    assert plan(day(offset), logged) == (None, [])


def test_already_sent_stage_is_never_resent():
    assert plan(day(-3), {"heads_up": "sent"}) == (None, [])
    assert plan(day(-1), {"heads_up": "sent"}) == (None, [])


def test_missed_stages_are_skipped_and_only_latest_is_sent():
    # Cron didn't run for two weeks: send only the final notice.
    assert plan(day(14)) == ("final_notice", ["heads_up", "due_today", "overdue"])


def test_late_run_sends_latest_due_stage_not_a_burst():
    assert plan(day(9), {"heads_up": "sent"}) == ("overdue", ["due_today"])


def test_skipped_rows_never_fire_later():
    logged = {"heads_up": "skipped", "due_today": "skipped"}
    assert plan(day(1), logged) == (None, [])


def test_no_reminder_on_the_day_the_invoice_was_sent():
    # Invoice emailed on its due date: due_today (and earlier) are covered by it.
    assert plan(day(0), sent=day(0)) == (None, ["heads_up", "due_today"])


def test_stage_dated_before_sent_date_is_skipped_but_later_stage_sends():
    # Sent one day before due: heads_up (day -3) predates sending -> skipped.
    assert plan(day(0), sent=day(-1)) == ("due_today", ["heads_up"])


def test_sent_late_after_due_date_waits_for_next_stage():
    # Invoice emailed 2 days after due date; nothing until the overdue stage.
    assert plan(day(2), sent=day(2)) == (None, ["heads_up", "due_today"])
    assert plan(day(3), {"heads_up": "skipped", "due_today": "skipped"}, sent=day(2)) == (None, [])
    assert plan(day(7), {"heads_up": "skipped", "due_today": "skipped"}, sent=day(2)) == ("overdue", [])


def test_failed_stage_is_retried_next_day():
    assert plan(day(1), {"heads_up": "sent", "due_today": "failed"}) == ("due_today", [])


def test_failed_stage_superseded_by_later_stage_is_skipped():
    logged = {"heads_up": "sent", "due_today": "sent", "overdue": "failed"}
    assert plan(day(14), logged) == ("final_notice", ["overdue"])


def test_pending_stage_is_not_retried():
    # A crash mid-send leaves "pending": never risk sending twice.
    assert plan(day(0), {"heads_up": "sent", "due_today": "pending"}) == (None, [])


def test_custom_stage_config():
    stages = get_stages(
        [
            {"key": "b", "offset_days": 5, "category": "c2", "subject": "s", "template": "t"},
            {"key": "a", "offset_days": -1, "category": "c1", "subject": "s", "template": "t"},
        ]
    )
    assert [s.key for s in stages] == ["a", "b"]
    result = plan_stages(due_date=DUE, sent_date=SENT, today=day(5), stages=stages, logged={})
    assert result.send.key == "b" and [s.key for s in result.skip] == ["a"]


def test_duplicate_stage_keys_rejected():
    item = {"key": "x", "offset_days": 0, "category": "c", "subject": "s", "template": "t"}
    with pytest.raises(ValueError):
        get_stages([item, item])
