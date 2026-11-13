import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import Engine
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.aoc.leaderboard import parse_leaderboard, persist_leaderboard
from app.config import ConfigurationError, Settings
from app.models import WorkerHeartbeat

logger = logging.getLogger(__name__)
WORKER_NAME = "aoc-poller"


class JsonFetcher(Protocol):
    def get_json(
        self, url: str, *, fixture_key: str | None = None
    ) -> dict[str, Any]: ...


def leaderboard_url(settings: Settings) -> str:
    leaderboard_id = settings.aoc_leaderboard_id
    if not leaderboard_id and settings.aoc_fixtures:
        leaderboard_id = "0"
    if not leaderboard_id or not leaderboard_id.isdecimal():
        raise ConfigurationError("AOC_LEADERBOARD_ID must be configured")
    return (
        "https://adventofcode.com/"
        f"{settings.aoc_year}/leaderboard/private/view/{leaderboard_id}.json"
    )


def run_poll_cycle(
    engine: Engine,
    settings: Settings,
    fetcher: JsonFetcher,
    *,
    clock: Callable[[], datetime] | None = None,
) -> int:
    now = _as_utc((clock or (lambda: datetime.now(UTC)))())
    try:
        payload = fetcher.get_json(leaderboard_url(settings))
        members = parse_leaderboard(payload, settings.aoc_year)
        persist_leaderboard(engine, members, last_seen_at=now)
    except Exception as error:
        consecutive_failures = _record_heartbeat(
            engine,
            now=now,
            error_type=type(error).__name__,
        )
        if consecutive_failures == 2:
            logger.warning(
                "AoC leaderboard polling failed twice consecutively",
                extra={
                    "worker": WORKER_NAME,
                    "consecutive_failures": consecutive_failures,
                    "error_type": type(error).__name__,
                },
            )
        raise

    _record_heartbeat(engine, now=now, error_type=None)
    return len(members)


def _record_heartbeat(engine: Engine, *, now: datetime, error_type: str | None) -> int:
    with Session(engine) as session:
        previous = session.get(WorkerHeartbeat, WORKER_NAME)
        failures = (
            previous.consecutive_failures + 1
            if error_type is not None and previous is not None
            else 1
            if error_type is not None
            else 0
        )
        last_success_at = (
            now.isoformat()
            if error_type is None
            else previous.last_success_at
            if previous is not None
            else None
        )
        values = {
            "worker_name": WORKER_NAME,
            "last_cycle_at": now.isoformat(),
            "last_success_at": last_success_at,
            "status": "error" if error_type is not None else "ok",
            "consecutive_failures": failures,
            "detail": error_type,
        }
        statement = insert(WorkerHeartbeat).values(**values)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[WorkerHeartbeat.worker_name],
                set_={
                    key: value for key, value in values.items() if key != "worker_name"
                },
            )
        )
        session.commit()
        return failures


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
