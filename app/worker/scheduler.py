import logging
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from datetime import time as datetime_time

logger = logging.getLogger(__name__)


def next_poll_at(after: datetime) -> datetime:
    now = _as_utc(after)
    for day_offset in range(3):
        run_date = now.date() + timedelta(days=day_offset)
        if run_date.month == 12 and 1 <= run_date.day <= 12:
            run_times: Iterable[datetime_time] = (
                datetime_time(hour=hour, minute=minute, tzinfo=UTC)
                for hour in range(24)
                for minute in (2, 17)
            )
        else:
            run_times = (datetime_time(hour=12, minute=2, tzinfo=UTC),)
        for run_time in run_times:
            candidate = datetime.combine(run_date, run_time)
            if candidate > now:
                return candidate
    raise RuntimeError("Unable to determine next AoC poll time")


def run_forever(
    poll_cycle: Callable[[], object],
    *,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    get_now = clock or (lambda: datetime.now(UTC))
    while True:
        now = _as_utc(get_now())
        sleep(max(0.0, (next_poll_at(now) - now).total_seconds()))
        try:
            poll_cycle()
        except Exception as error:
            logger.error(
                "Scheduled AoC poll cycle failed",
                extra={"error_type": type(error).__name__},
            )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
