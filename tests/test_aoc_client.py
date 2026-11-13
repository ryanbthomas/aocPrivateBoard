import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.aoc.client import AoCError, AoCFetchClient, SessionExpired
from app.config import Settings
from app.db import create_database_engine
from app.models import AuditLog, Base, Fetch

LEADERBOARD_URL = "https://adventofcode.com/2025/leaderboard/private/view/123456.json"
PAYLOAD = {"event": "2025", "members": {}}
SESSION_COOKIE = "never-log-this-session-secret"


@pytest.fixture
def engine(tmp_path: Any) -> Engine:
    database_engine = create_database_engine(f"sqlite:///{tmp_path}/client.db")
    Base.metadata.create_all(database_engine)
    yield database_engine
    database_engine.dispose()


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2025, 12, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _client(
    handler: Any,
    *,
    engine: Engine,
    settings: Settings | None = None,
    clock: Clock | None = None,
    sleep: Any = lambda _delay: None,
) -> tuple[AoCFetchClient, httpx2.Client]:
    http_client = httpx2.Client(transport=httpx2.MockTransport(handler))
    return (
        AoCFetchClient(
            engine,
            settings or Settings(),
            client=http_client,
            clock=clock,
            sleep=sleep,
        ),
        http_client,
    )


def _success(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json=PAYLOAD, request=request)


def test_persists_fresh_cache_across_client_instances(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AOC_SESSION", SESSION_COOKIE)
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _success(request)

    first, first_http = _client(handler, engine=engine)
    second, second_http = _client(handler, engine=engine)
    try:
        assert first.get_json(LEADERBOARD_URL) == PAYLOAD
        assert second.get_json(LEADERBOARD_URL) == PAYLOAD
    finally:
        first_http.close()
        second_http.close()

    assert len(requests) == 1
    assert (
        requests[0]
        .headers["user-agent"]
        .endswith("https://github.com/ryanbthomas/aocPrivateBoard)")
    )
    assert requests[0].headers["cookie"] == f"session={SESSION_COOKIE}"
    assert requests[0].extensions["timeout"] == {
        "connect": 20.0,
        "read": 20.0,
        "write": 20.0,
        "pool": 20.0,
    }


def test_expired_cache_sends_validators_and_304_refreshes_ttl(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AOC_SESSION", SESSION_COOKIE)
    clock = Clock()
    requests: list[httpx2.Request] = []
    refreshed_at = clock.now + timedelta(seconds=900)

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(
                200,
                json=PAYLOAD,
                headers={
                    "ETag": '"etag-1"',
                    "Last-Modified": "Mon, 01 Dec 2025 00:00:00 GMT",
                },
                request=request,
            )
        return httpx2.Response(304, request=request)

    fetcher, http_client = _client(handler, engine=engine, clock=clock)
    try:
        assert fetcher.get_json(LEADERBOARD_URL) == PAYLOAD
        clock.now += timedelta(seconds=900)
        assert fetcher.get_json(LEADERBOARD_URL) == PAYLOAD
        assert requests[1].headers["if-none-match"] == '"etag-1"'
        assert requests[1].headers["if-modified-since"] == (
            "Mon, 01 Dec 2025 00:00:00 GMT"
        )
        clock.now += timedelta(seconds=899)
        assert fetcher.get_json(LEADERBOARD_URL) == PAYLOAD
        assert len(requests) == 2
    finally:
        http_client.close()

    with Session(engine) as db:
        cached = db.get(Fetch, LEADERBOARD_URL)
    assert cached is not None
    assert cached.payload == json.dumps(PAYLOAD, separators=(",", ":")).encode()
    assert cached.etag == '"etag-1"'
    assert cached.fetched_at == refreshed_at.isoformat()


def test_concurrent_calls_are_coalesced(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AOC_SESSION", SESSION_COOKIE)
    started = threading.Event()
    release = threading.Event()
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        started.set()
        assert release.wait(timeout=5)
        return _success(request)

    first, first_http = _client(handler, engine=engine)
    second, second_http = _client(handler, engine=engine)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_result = pool.submit(first.get_json, LEADERBOARD_URL)
            assert started.wait(timeout=5)
            second_result = pool.submit(second.get_json, LEADERBOARD_URL)
            release.set()
            assert first_result.result(timeout=5) == PAYLOAD
            assert second_result.result(timeout=5) == PAYLOAD
    finally:
        first_http.close()
        second_http.close()

    assert len(requests) == 1


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (302, ""),
        (401, ""),
        (403, ""),
        (200, "<html>Please log in to get your puzzle input</html>"),
    ],
)
def test_expired_session_is_audited_once_per_incident_without_secret_leaks(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    status_code: int,
    body: str,
) -> None:
    monkeypatch.setenv("AOC_SESSION", SESSION_COOKIE)
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(status_code, text=body, request=request)

    fetcher, http_client = _client(handler, engine=engine)
    try:
        with caplog.at_level(logging.WARNING, logger="app.aoc.client"):
            for _ in range(2):
                with pytest.raises(SessionExpired):
                    fetcher.get_json(LEADERBOARD_URL)
    finally:
        http_client.close()

    with Session(engine) as db:
        audit_rows = db.scalars(
            select(AuditLog).where(AuditLog.action == "aoc.session_expired")
        ).all()
    assert len(audit_rows) == 1
    assert len(requests) == 2
    assert audit_rows[0].target == LEADERBOARD_URL
    assert "session expired" in caplog.text.lower()
    assert SESSION_COOKIE not in caplog.text


def test_retries_server_errors_with_bounded_backoff(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AOC_SESSION", SESSION_COOKIE)
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(503, request=request)

    fetcher, http_client = _client(handler, engine=engine, sleep=delays.append)
    try:
        with pytest.raises(AoCError, match="HTTP 503"):
            fetcher.get_json(LEADERBOARD_URL)
    finally:
        http_client.close()

    assert attempts == 3
    assert delays == [0.5, 1.0]


def test_retries_rate_limit_using_bounded_retry_after(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AOC_SESSION", SESSION_COOKIE)
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(429, headers={"Retry-After": "45"}, request=request)

    fetcher, http_client = _client(handler, engine=engine, sleep=delays.append)
    try:
        with pytest.raises(AoCError, match="HTTP 429"):
            fetcher.get_json(LEADERBOARD_URL)
    finally:
        http_client.close()

    assert attempts == 3
    assert delays == [20.0, 20.0]


def test_invalid_json_fails_without_caching_or_logging_response_body(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("AOC_SESSION", SESSION_COOKIE)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            text=f"not-json {SESSION_COOKIE}",
            request=request,
        )

    fetcher, http_client = _client(handler, engine=engine)
    try:
        with (
            caplog.at_level(logging.WARNING, logger="app.aoc.client"),
            pytest.raises(AoCError, match="invalid JSON") as error,
        ):
            fetcher.get_json(LEADERBOARD_URL)
    finally:
        http_client.close()

    assert SESSION_COOKIE not in str(error.value)
    assert SESSION_COOKIE not in caplog.text
    with Session(engine) as db:
        assert db.get(Fetch, LEADERBOARD_URL) is None


def test_fixture_mode_uses_existing_fixture_without_http(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AOC_FIXTURES", "1")

    def no_network(_request: httpx2.Request) -> httpx2.Response:
        pytest.fail("fixture mode must not make an HTTP request")

    fetcher, http_client = _client(
        no_network, engine=engine, settings=Settings(aoc_fixtures=True)
    )
    try:
        leaderboard = fetcher.get_json(LEADERBOARD_URL)
    finally:
        http_client.close()

    assert leaderboard["event"] == "2025"
    with Session(engine) as db:
        assert db.scalar(select(Fetch)) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://adventofcode.com/2025/leaderboard/private/view/123.json",
        "https://example.com/2025/leaderboard/private/view/123.json",
        "https://adventofcode.com/2025/day/1",
        "https://adventofcode.com/2025/leaderboard/private/view/.json",
    ],
)
def test_rejects_non_leaderboard_or_external_urls(engine: Engine, url: str) -> None:
    fetcher, http_client = _client(lambda request: _success(request), engine=engine)
    try:
        with pytest.raises(ValueError, match="only accepts private leaderboard"):
            fetcher.get_json(url)
    finally:
        http_client.close()
