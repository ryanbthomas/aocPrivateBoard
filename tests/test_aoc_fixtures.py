from collections.abc import Iterator

import httpx2
import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from app.aoc.client import AoCFetchClient
from app.config import ConfigurationError, Settings
from app.models import Base, Fetch

LEADERBOARD_URL = "https://adventofcode.com/2025/leaderboard/private/view/123456.json"


@pytest.fixture
def engine() -> Iterator[Engine]:
    database_engine = create_engine("sqlite://")
    Base.metadata.create_all(database_engine)
    yield database_engine
    database_engine.dispose()


def test_aoc_fixture_mode_loads_anonymized_leaderboard(engine: Engine) -> None:
    def no_network(request: httpx2.Request) -> httpx2.Response:
        pytest.fail(f"fixture mode made an HTTP request to {request.url}")

    http_client = httpx2.Client(transport=httpx2.MockTransport(no_network))
    fetcher = AoCFetchClient(
        engine,
        Settings(aoc_fixtures=True),
        client=http_client,
    )
    try:
        leaderboard = fetcher.get_json(LEADERBOARD_URL)
    finally:
        http_client.close()

    members_data = leaderboard["members"]
    assert isinstance(members_data, dict)
    members = [member for member in members_data.values() if isinstance(member, dict)]

    assert leaderboard["event"] == "2025"
    assert leaderboard["num_days"] == 12
    assert len(members_data) == len(members) == 15
    assert all(
        isinstance(member.get("name"), str) and member["name"].startswith("Member ")
        for member in members
    )
    assert any(member.get("stars") == 0 for member in members)
    assert any(member.get("completion_day_level") for member in members)
    with Session(engine) as db:
        assert db.scalar(select(Fetch)) is None


def test_fixture_mode_rejects_non_leaderboard_urls(engine: Engine) -> None:
    http_client = httpx2.Client(
        transport=httpx2.MockTransport(
            lambda request: pytest.fail(f"unexpected HTTP request to {request.url}")
        )
    )
    fetcher = AoCFetchClient(
        engine,
        Settings(aoc_fixtures=True),
        client=http_client,
    )
    try:
        with pytest.raises(ValueError, match="only accepts private leaderboard"):
            fetcher.get_json(
                "https://adventofcode.com/leaderboard/private/view/123456.json"
            )
    finally:
        http_client.close()


def test_live_mode_does_not_fall_back_to_fixture(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AOC_SESSION", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    http_client = httpx2.Client(
        transport=httpx2.MockTransport(
            lambda request: pytest.fail(f"unexpected HTTP request to {request.url}")
        )
    )
    fetcher = AoCFetchClient(
        engine,
        Settings(app_env="prod", aoc_fixtures=False),
        client=http_client,
    )
    try:
        with pytest.raises(ConfigurationError, match="AOC_SESSION is required"):
            fetcher.get_json(LEADERBOARD_URL)
    finally:
        http_client.close()
