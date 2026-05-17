from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.types import JSON
from werkzeug.security import check_password_hash, generate_password_hash

from state import load_json


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///data/worldcup_ai.db")
JSONType = JSONB if DATABASE_URL.startswith("postgresql") else JSON

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    preferences: Mapped["UserPreference"] = relationship(back_populates="user", cascade="all, delete-orphan")

    def verify_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="zh", nullable=False)
    theme: Mapped[str] = mapped_column(String(16), default="dark", nullable=False)
    risk_style: Mapped[str] = mapped_column(String(32), default="aggressive", nullable=False)
    watchlist: Mapped[dict[str, Any]] = mapped_column(JSONType, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="preferences")


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    season: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    country: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_match_source_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False)
    competition_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    season: Mapped[str] = mapped_column(String(32), default="", index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    group_name: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    matchday: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    kickoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    home_team_code: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    home_team_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    away_team_code: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    away_team_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="upcoming", index=True, nullable=False)
    score_home: Mapped[int | None] = mapped_column(Integer)
    score_away: Mapped[int | None] = mapped_column(Integer)
    venue: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    city: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id", ondelete="SET NULL"))
    competition_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    bookmaker: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    market: Mapped[str] = mapped_column(String(40), nullable=False)
    selection: Mapped[str] = mapped_column(String(80), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    point: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(80), default="openclaw", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    markdown: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), default="manual", index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)


def init_db() -> None:
    Path("data").mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def password_hash(password: str) -> str:
    return generate_password_hash(password)


def seed_competitions(session) -> None:
    rows = [
        {"key": "worldcup", "name": "FIFA World Cup 2026", "name_zh": "世界杯", "season": "2026", "country": "Global", "source": "FIFA API"},
        {"key": "epl", "name": "Premier League", "name_zh": "英超", "season": "2025", "country": "England", "source": "football-data.org/openfootball"},
    ]
    for row in rows:
        existing = session.scalar(select(Competition).where(Competition.key == row["key"]))
        if existing:
            for key, value in row.items():
                setattr(existing, key, value)
        else:
            session.add(Competition(**row))


def seed_teams(session) -> int:
    rows = load_json("data/teams.json", [])
    count = 0
    for row in rows:
        code = str(row.get("code") or row.get("name") or "").strip()
        if not code:
            continue
        aliases = row.get("aliases") or []
        name_zh = aliases[0] if aliases else ""
        existing = session.scalar(select(Team).where(Team.code == code))
        payload = {
            "code": code,
            "name": row.get("name", code),
            "name_zh": name_zh,
            "aliases": aliases,
            "profile": row,
        }
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
        else:
            session.add(Team(**payload))
        count += 1
    return count


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def seed_worldcup_matches(session) -> int:
    rows = load_json("data/worldcup_2026_schedule.json", [])
    count = 0
    for row in rows:
        source_id = row.get("match_id") or row.get("fifa_match_id") or str(row.get("match_number", ""))
        if not source_id:
            continue
        existing = session.scalar(select(Match).where(Match.source == "fifa", Match.source_id == source_id))
        payload = {
            "source": "fifa",
            "source_id": source_id,
            "competition_key": "worldcup",
            "season": "2026",
            "stage": row.get("stage", ""),
            "group_name": row.get("group", ""),
            "matchday": str(row.get("match_number") or ""),
            "kickoff_time": parse_dt(row["kickoff_time"]),
            "home_team_code": row.get("home_team_code", ""),
            "home_team_name": row.get("home_team", ""),
            "away_team_code": row.get("away_team_code", ""),
            "away_team_name": row.get("away_team", ""),
            "status": "upcoming",
            "venue": row.get("venue", ""),
            "city": row.get("city", ""),
            "raw": row,
        }
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
        else:
            session.add(Match(**payload))
        count += 1
    return count


def seed_all() -> dict[str, int]:
    init_db()
    with session_scope() as session:
        seed_competitions(session)
        team_count = seed_teams(session)
        worldcup_count = seed_worldcup_matches(session)
        return {"teams": team_count, "worldcup_matches": worldcup_count}


def db_counts() -> dict[str, int]:
    with session_scope() as session:
        return {
            "users": len(session.scalars(select(User.id)).all()),
            "teams": len(session.scalars(select(Team.id)).all()),
            "matches": len(session.scalars(select(Match.id)).all()),
            "odds_snapshots": len(session.scalars(select(OddsSnapshot.id)).all()),
            "analysis_results": len(session.scalars(select(AnalysisResult.id)).all()),
            "sync_runs": len(session.scalars(select(SyncRun.id)).all()),
        }
