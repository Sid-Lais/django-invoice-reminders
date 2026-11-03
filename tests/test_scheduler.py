from unittest.mock import Mock

from django.core.management.base import CommandError

from docker import scheduler


def test_scheduler_runs_hourly_and_continues_after_delivery_failure(monkeypatch, caplog):
    stop = Mock()
    stop.is_set.side_effect = [False, False, True]
    command = Mock(side_effect=[CommandError("delivery failed"), None])
    monkeypatch.setattr(scheduler, "call_command", command)
    clock = iter([0, 12, 3600, 3605])
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: next(clock))

    scheduler.run_scheduler(stop)

    assert command.call_count == 2
    command.assert_called_with("send_payment_reminders")
    assert [call.args for call in stop.wait.call_args_list] == [(3588,), (3595,)]
    assert "retrying next hour" in caplog.text


def test_scheduler_can_stop_before_first_run(monkeypatch):
    stop = Mock()
    stop.is_set.return_value = True
    command = Mock()
    monkeypatch.setattr(scheduler, "call_command", command)

    scheduler.run_scheduler(stop)

    command.assert_not_called()
    stop.wait.assert_not_called()
