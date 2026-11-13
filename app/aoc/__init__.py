import json
from pathlib import Path
from urllib.parse import urlparse

from app.config import Settings

LEADERBOARD_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def json_url(url: str) -> dict[str, object]:
    if Settings().aoc_fixtures:
        path_parts = urlparse(url).path.strip("/").split("/")
        year = path_parts[0] if path_parts else ""
        if len(year) != 4 or not year.isdecimal():
            raise ValueError(f"Unable to determine AoC year from URL: {url}")

        fixture_path = LEADERBOARD_FIXTURES / f"leaderboard_{year}.json"
        with fixture_path.open(encoding="utf-8") as fixture:
            return json.load(fixture)

    raise RuntimeError("AOC_FIXTURES must be set to 1 to load leaderboard fixtures")
