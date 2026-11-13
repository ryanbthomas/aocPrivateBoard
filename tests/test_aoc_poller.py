import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.aoc.client import AoCFetchClient
from app.aoc.leaderboard import LeaderboardFormatError, parse_leaderboard
from app.aoc.poller import WORKER_NAME, run_poll_cycle
from app.config import Settings
from app.db import create_database_engine
from app.models import AocMember, Base, Star, WorkerHeartbeat
from app.worker.scheduler import next_poll_at

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "leaderboard_2025.json"
POLL_TIME = datetime(2025, 12, 1, 0, 2, tzinfo=UTC)


@pytest.fixture
def engine() -> Engine:
    database_engine = create_database_engine("sqlite://")
    Base.metadata.create_all(database_engine)
    yield database_engine
    database_engine.dispose()


class FixtureFetcher:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.urls: list[str] = []
        self.error: Exception | None = None

    def get_json(self, url: str, *, fixture_key: str | None = None) -> dict[str, Any]:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.payload


def _fixture_payload() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as fixture:
        payload = json.load(fixture)
    assert isinstance(payload, dict)
    return payload


def test_polling_twice_is_idempotent_and_member_rename_preserves_identity(
    engine: Engine,
) -> None:
    payload = _fixture_payload()
    fetcher = FixtureFetcher(payload)
    settings = Settings(
        aoc_year=2025,
        aoc_leaderboard_id="123456",
        aoc_fixtures=True,
    )

    first_count = run_poll_cycle(engine, settings, fetcher, clock=lambda: POLL_TIME)
    member = payload["members"]["9000002"]
    assert isinstance(member, dict)
    member["name"] = "Renamed Member"
    second_count = run_poll_cycle(engine, settings, fetcher, clock=lambda: POLL_TIME)

    with Session(engine) as session:
        member_rows = session.scalars(select(AocMember)).all()
        star_rows = session.scalars(select(Star)).all()
        renamed = session.get(AocMember, "9000002")
        heartbeat = session.get(WorkerHeartbeat, WORKER_NAME)

    assert first_count == second_count == 15
    assert len(fetcher.urls) == 2
    assert fetcher.urls[0] == (
        "https://adventofcode.com/2025/leaderboard/private/view/123456.json"
    )
    assert len(member_rows) == 15
    assert len(star_rows) == 70
    assert renamed is not None
    assert renamed.name == "Renamed Member"
    assert renamed.id == "9000002"
    assert heartbeat is not None
    assert heartbeat.status == "ok"
    assert heartbeat.consecutive_failures == 0
    assert heartbeat.last_success_at == POLL_TIME.isoformat()


def test_fixture_client_drives_a_complete_poll_cycle_without_network(
    engine: Engine,
) -> None:
    def no_network(request: httpx2.Request) -> httpx2.Response:
        pytest.fail(f"fixture poll unexpectedly requested {request.url}")

    http_client = httpx2.Client(transport=httpx2.MockTransport(no_network))
    settings = Settings(
        aoc_year=2025,
        aoc_leaderboard_id="123456",
        aoc_fixtures=True,
    )
    try:
        with AoCFetchClient(engine, settings, client=http_client) as fetcher:
            assert (
                run_poll_cycle(engine, settings, fetcher, clock=lambda: POLL_TIME) == 15
            )
    finally:
        http_client.close()

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AocMember)) == 15
        assert session.scalar(select(func.count()).select_from(Star)) == 70
        assert session.get(WorkerHeartbeat, WORKER_NAME) is not None


def test_parser_rejects_malformed_records_before_persistence() -> None:
    with pytest.raises(LeaderboardFormatError, match="event"):
        parse_leaderboard({"event": "2024", "members": {}}, 2025)

    with pytest.raises(LeaderboardFormatError, match="timestamp"):
        parse_leaderboard(
            {
                "event": "2025",
                "members": {
                    "1": {
                        "id": 1,
                        "completion_day_level": {
                            "1": {"1": {"get_star_ts": "invalid"}}
                        },
                    }
                },
            },
            2025,
        )


def test_two_consecutive_failures_emit_one_warning_and_recovery_resets_streak(
    engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fetcher = FixtureFetcher(_fixture_payload())
    settings = Settings(aoc_year=2025, aoc_leaderboard_id="123456")
    with caplog.at_level(logging.WARNING, logger="app.aoc.poller"):
        run_poll_cycle(engine, settings, fetcher, clock=lambda: POLL_TIME)
        fetcher.error = RuntimeError("synthetic failure")
        for _ in range(2):
            with pytest.raises(RuntimeError, match="synthetic failure"):
                run_poll_cycle(engine, settings, fetcher, clock=lambda: POLL_TIME)

        assert caplog.text.count("failed twice consecutively") == 1
        fetcher.error = None
        run_poll_cycle(engine, settings, fetcher, clock=lambda: POLL_TIME)

    with Session(engine) as session:
        heartbeat = session.get(WorkerHeartbeat, WORKER_NAME)
    assert heartbeat is not None
    assert heartbeat.status == "ok"
    assert heartbeat.consecutive_failures == 0
    assert heartbeat.last_success_at == POLL_TIME.isoformat()


def test_failure_updates_heartbeat_without_storing_error_message_or_secret(
    engine: Engine,
) -> None:
    secret = "do-not-persist-this"
    fetcher = FixtureFetcher(_fixture_payload())
    fetcher.error = RuntimeError(secret)
    settings = Settings(aoc_year=2025, aoc_leaderboard_id="123456")

    with pytest.raises(RuntimeError, match=secret):
        run_poll_cycle(engine, settings, fetcher, clock=lambda: POLL_TIME)

    with Session(engine) as session:
        heartbeat = session.get(WorkerHeartbeat, WORKER_NAME)
    assert heartbeat is not None
    assert heartbeat.status == "error"
    assert heartbeat.consecutive_failures == 1
    assert heartbeat.detail == "RuntimeError"
    assert secret not in str(heartbeat.detail)


def test_worker_once_entrypoint_runs_a_single_poll_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.__main__ as worker_main

    engine = create_database_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    settings = Settings(
        aoc_year=2025,
        aoc_leaderboard_id="123456",
        aoc_fixtures=True,
    )
    fetcher = FixtureFetcher(_fixture_payload())

    class FetcherContext:
        def __enter__(self) -> FixtureFetcher:
            return fetcher

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(worker_main, "load_settings", lambda _process: settings)
    monkeypatch.setattr(
        worker_main, "create_database_engine", lambda _database_url: engine
    )
    monkeypatch.setattr(
        worker_main,
        "AoCFetchClient",
        lambda _engine, _settings: FetcherContext(),
    )

    assert worker_main.main(["--once"]) == 0
    assert len(fetcher.urls) == 1
    with Session(engine) as session:
        assert session.get(WorkerHeartbeat, WORKER_NAME) is not None
    engine.dispose()


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        (
            datetime(2025, 12, 1, 0, 1, tzinfo=UTC),
            datetime(2025, 12, 1, 0, 2, tzinfo=UTC),
        ),
        (
            datetime(2025, 12, 1, 0, 2, tzinfo=UTC),
            datetime(2025, 12, 1, 0, 17, tzinfo=UTC),
        ),
        (
            datetime(2025, 12, 12, 23, 17, tzinfo=UTC),
            datetime(2025, 12, 13, 12, 2, tzinfo=UTC),
        ),
        (
            datetime(2025, 11, 30, 12, 3, tzinfo=UTC),
            datetime(2025, 12, 1, 0, 2, tzinfo=UTC),
        ),
        (
            datetime(2025, 12, 13, 12, 2, tzinfo=UTC),
            datetime(2025, 12, 14, 12, 2, tzinfo=UTC),
        ),
    ],
)
def test_next_poll_at_respects_season_boundaries(
    after: datetime, expected: datetime
) -> None:
    assert next_poll_at(after) == expected


def test_leaderboard_parse_is_not_persisted_if_any_member_is_malformed(
    engine: Engine,
) -> None:
    payload = _fixture_payload()
    members = payload["members"]
    assert isinstance(members, dict)
    members["malformed"] = {"id": 42}
    fetcher = FixtureFetcher(payload)
    settings = Settings(aoc_year=2025, aoc_leaderboard_id="123456")

    with pytest.raises(LeaderboardFormatError, match="does not match"):
        run_poll_cycle(engine, settings, fetcher, clock=lambda: POLL_TIME)

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AocMember)) == 0
        assert session.scalar(select(func.count()).select_from(Star)) == 0
