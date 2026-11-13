from __future__ import annotations

from sqlalchemy import (
    REAL,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "claim_status IS NULL OR claim_status IN ('none', 'pending', 'approved')",
            name="ck_users_claim_status",
        ),
        CheckConstraint(
            "role IS NULL OR role IN ('member', 'admin')",
            name="ck_users_role",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    github_login: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    aoc_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("aoc_members.id"), unique=True
    )
    claim_status: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class AocMember(Base):
    __tablename__ = "aoc_members"
    __table_args__ = (Index("ix_aoc_members_year", "year"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    local_score: Mapped[int | None] = mapped_column(Integer)
    stars: Mapped[int | None] = mapped_column(Integer)
    last_seen_at: Mapped[str] = mapped_column(Text, nullable=False)


class Star(Base):
    __tablename__ = "stars"
    __table_args__ = (
        CheckConstraint("day BETWEEN 1 AND 12", name="ck_stars_day"),
        CheckConstraint("part BETWEEN 1 AND 2", name="ck_stars_part"),
    )

    aoc_member_id: Mapped[str] = mapped_column(
        ForeignKey("aoc_members.id"), primary_key=True
    )
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[int] = mapped_column(Integer, primary_key=True)
    part: Mapped[int] = mapped_column(Integer, primary_key=True)
    solved_at: Mapped[str] = mapped_column(Text, nullable=False)


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        Index("ix_submissions_year_day_language", "year", "day", "language"),
        Index("ix_submissions_user_id", "user_id"),
        Index("ix_submissions_sha256", "sha256"),
        CheckConstraint("part IS NULL OR part IN (1, 2)", name="ck_submissions_part"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    day: Mapped[int] = mapped_column(Integer, nullable=False)
    part: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)


class SubmissionTag(Base):
    __tablename__ = "submission_tags"

    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)


class Analysis(Base):
    __tablename__ = "analysis"

    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id"), primary_key=True
    )
    syntax_ok: Mapped[int | None] = mapped_column(Integer)
    loc: Mapped[int | None] = mapped_column(Integer)
    comment_ratio: Mapped[float | None] = mapped_column(REAL)
    max_depth: Mapped[int | None] = mapped_column(Integer)
    complexity: Mapped[int | None] = mapped_column(Integer)
    identifiers: Mapped[int | None] = mapped_column(Integer)
    longest_line: Mapped[int | None] = mapped_column(Integer)
    ast_histogram: Mapped[str | None] = mapped_column(Text)
    analysed_at: Mapped[str] = mapped_column(Text, nullable=False)
    analyser_version: Mapped[str] = mapped_column(Text, nullable=False)


class Similarity(Base):
    __tablename__ = "similarity"
    __table_args__ = (
        CheckConstraint("a_id < b_id", name="ck_similarity_order"),
        CheckConstraint("score BETWEEN 0 AND 1", name="ck_similarity_score"),
    )

    a_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    b_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    method: Mapped[str] = mapped_column(Text, primary_key=True)
    score: Mapped[float] = mapped_column(REAL, nullable=False)
    computed_at: Mapped[str] = mapped_column(Text, nullable=False)


class Fetch(Base):
    __tablename__ = "fetches"

    url: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[bytes | None] = mapped_column(LargeBinary)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeat"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'error')", name="ck_worker_heartbeat_status"),
        CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_worker_heartbeat_consecutive_failures",
        ),
    )

    worker_name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_cycle_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_success_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
