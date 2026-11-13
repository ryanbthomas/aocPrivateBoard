import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db import create_database_engine


def test_sqlite_pragmas_are_applied_on_connect(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "pragmas.db"
    engine = create_database_engine(f"sqlite:///{database_path}")
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
            assert connection.scalar(text("PRAGMA busy_timeout")) == 5000
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert connection.scalar(text("PRAGMA synchronous")) == 1
    finally:
        engine.dispose()


def test_baseline_migrates_an_empty_sqlite_database(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "baseline.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    config = Config("alembic.ini")

    command.upgrade(config, "head")


def test_upgrade_removes_legacy_puzzles_table(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "legacy.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    config = Config("alembic.ini")

    command.upgrade(config, "ddc45664563f")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE puzzles "
            "(year INTEGER NOT NULL, day INTEGER NOT NULL, html TEXT NOT NULL, "
            "fetched_at TEXT NOT NULL, PRIMARY KEY (year, day))"
        )

    command.upgrade(config, "head")
    command.check(config)

    with sqlite3.connect(database_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "puzzles" not in table_names
    command.check(config)

    with sqlite3.connect(database_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert table_names == {
            "users",
            "aoc_members",
            "stars",
            "submissions",
            "tags",
            "submission_tags",
            "analysis",
            "similarity",
            "fetches",
            "audit_log",
            "alembic_version",
        }

    engine = create_database_engine(f"sqlite:///{database_path}")
    try:
        inspector = inspect(engine)
        assert {
            "ix_aoc_members_year",
            "ix_submissions_year_day_language",
            "ix_submissions_user_id",
            "ix_submissions_sha256",
        } <= {index["name"] for index in inspector.get_indexes("submissions")} | {
            index["name"] for index in inspector.get_indexes("aoc_members")
        }
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO aoc_members (id, year, last_seen_at) "
                    "VALUES ('member-1', 2025, '2025-12-01T00:00:00Z')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO users (github_id, github_login, aoc_member_id, "
                    "created_at) VALUES ('github-1', 'user-1', 'member-1', 'now')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO users (github_id, github_login, created_at) "
                    "VALUES ('github-2', 'user-2', 'now')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO submissions (user_id, year, day, sha256, language, "
                    "bytes, created_at) VALUES (1, 2025, 1, 'hash', 'python', 1, 'now')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO tags (name, category) "
                    "VALUES ('queue', 'data_structure')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO submission_tags (submission_id, tag_id) VALUES (1, 1)"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (github_id, github_login, aoc_member_id, "
                "created_at) VALUES ('github-3', 'user-3', 'member-1', 'now')"
            )
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("INSERT INTO submission_tags (submission_id, tag_id) VALUES (1, 1)")
        )

    command.upgrade(config, "head")
