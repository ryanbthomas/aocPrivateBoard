import pytest

from app.aoc import json_url

LEADERBOARD_URL = "https://adventofcode.com/2025/leaderboard/private/view/123456.json"


def test_aoc_fixture_mode_loads_anonymized_leaderboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AOC_FIXTURES", "1")

    leaderboard = json_url(LEADERBOARD_URL)
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


def test_fixture_mode_requires_a_valid_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AOC_FIXTURES", "1")

    with pytest.raises(ValueError, match="Unable to determine AoC year"):
        json_url("https://adventofcode.com/leaderboard/private/view/123456.json")


def test_fixture_mode_must_be_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AOC_FIXTURES", raising=False)

    with pytest.raises(RuntimeError, match="AOC_FIXTURES must be set to 1"):
        json_url(LEADERBOARD_URL)
