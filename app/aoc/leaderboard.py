from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.models import AocMember, Star


class LeaderboardFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedStar:
    aoc_member_id: str
    year: int
    day: int
    part: int
    solved_at: str


@dataclass(frozen=True)
class ParsedMember:
    id: str
    year: int
    name: str | None
    local_score: int | None
    stars: int | None
    solved_stars: tuple[ParsedStar, ...]


def parse_leaderboard(payload: dict[str, Any], year: int) -> list[ParsedMember]:
    members = payload.get("members")
    if not isinstance(members, dict):
        raise LeaderboardFormatError("Leaderboard members must be an object")

    event = payload.get("event")
    if event is not None and str(event) != str(year):
        raise LeaderboardFormatError("Leaderboard event does not match configured year")

    parsed_members: list[ParsedMember] = []
    for member_key, raw_member in members.items():
        if not isinstance(member_key, str) or not isinstance(raw_member, dict):
            raise LeaderboardFormatError("Leaderboard member record is malformed")

        member_id = raw_member.get("id")
        if isinstance(member_id, bool) or not isinstance(member_id, (int, str)):
            raise LeaderboardFormatError("Leaderboard member ID is invalid")
        member_id = str(member_id)
        if not member_id or member_id != member_key:
            raise LeaderboardFormatError("Leaderboard member ID does not match its key")

        name = raw_member.get("name")
        if name is not None and not isinstance(name, str):
            raise LeaderboardFormatError("Leaderboard member name is invalid")

        local_score = _optional_integer(raw_member.get("local_score"), "local_score")
        total_stars = _optional_integer(raw_member.get("stars"), "stars")
        completion = raw_member.get("completion_day_level", {})
        if not isinstance(completion, dict):
            raise LeaderboardFormatError("Leaderboard completion data is malformed")

        parsed_stars: list[ParsedStar] = []
        for day_value, parts in completion.items():
            day = _parse_integer_string(day_value, "day")
            if not 1 <= day <= 12 or not isinstance(parts, dict):
                raise LeaderboardFormatError("Leaderboard completion day is invalid")
            for part_value, completion_data in parts.items():
                part = _parse_integer_string(part_value, "part")
                if part not in (1, 2) or not isinstance(completion_data, dict):
                    raise LeaderboardFormatError(
                        "Leaderboard completion part is invalid"
                    )
                solved_at = completion_data.get("get_star_ts")
                if isinstance(solved_at, bool) or not isinstance(solved_at, (int, str)):
                    raise LeaderboardFormatError(
                        "Leaderboard star timestamp is invalid"
                    )
                try:
                    epoch = int(solved_at)
                    solved_at_iso = datetime.fromtimestamp(epoch, UTC).isoformat()
                except (OverflowError, OSError, TypeError, ValueError) as error:
                    raise LeaderboardFormatError(
                        "Leaderboard star timestamp is invalid"
                    ) from error
                if epoch <= 0:
                    raise LeaderboardFormatError(
                        "Leaderboard star timestamp is invalid"
                    )
                parsed_stars.append(
                    ParsedStar(
                        aoc_member_id=member_id,
                        year=year,
                        day=day,
                        part=part,
                        solved_at=solved_at_iso,
                    )
                )

        parsed_members.append(
            ParsedMember(
                id=member_id,
                year=year,
                name=name,
                local_score=local_score,
                stars=total_stars,
                solved_stars=tuple(parsed_stars),
            )
        )

    return parsed_members


def persist_leaderboard(
    engine: Engine,
    members: list[ParsedMember],
    *,
    last_seen_at: datetime | None = None,
) -> None:
    seen_at = _as_utc(last_seen_at or datetime.now(UTC)).isoformat()
    member_values = [
        {
            "id": member.id,
            "year": member.year,
            "name": member.name,
            "local_score": member.local_score,
            "stars": member.stars,
            "last_seen_at": seen_at,
        }
        for member in members
    ]
    star_values = [
        {
            "aoc_member_id": star.aoc_member_id,
            "year": star.year,
            "day": star.day,
            "part": star.part,
            "solved_at": star.solved_at,
        }
        for member in members
        for star in member.solved_stars
    ]

    with Session(engine) as session, session.begin():
        if member_values:
            member_insert = insert(AocMember).values(member_values)
            session.execute(
                member_insert.on_conflict_do_update(
                    index_elements=[AocMember.id],
                    set_={
                        "year": member_insert.excluded.year,
                        "name": member_insert.excluded.name,
                        "local_score": member_insert.excluded.local_score,
                        "stars": member_insert.excluded.stars,
                        "last_seen_at": member_insert.excluded.last_seen_at,
                    },
                )
            )
        if star_values:
            star_insert = insert(Star).values(star_values)
            session.execute(
                star_insert.on_conflict_do_update(
                    index_elements=[
                        Star.aoc_member_id,
                        Star.year,
                        Star.day,
                        Star.part,
                    ],
                    set_={"solved_at": star_insert.excluded.solved_at},
                )
            )


def _optional_integer(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise LeaderboardFormatError(f"Leaderboard {field} is invalid")
    return value


def _parse_integer_string(value: Any, field: str) -> int:
    if not isinstance(value, str) or not value.isdecimal():
        raise LeaderboardFormatError(f"Leaderboard {field} is invalid")
    return int(value)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
