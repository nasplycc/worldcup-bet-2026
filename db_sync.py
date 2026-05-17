from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from db import AnalysisResult, Match, OddsSnapshot, session_scope
from state import load_json


def parse_match_datetime(date: str, time: str = "") -> datetime:
    value = f"{date}T{time or '00:00'}:00+08:00" if len(time or "") <= 5 else f"{date}T{time}+08:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def upsert_matches_from_frontend(matches: list[dict[str, Any]], competition_key: str, season: str, source: str) -> int:
    count = 0
    with session_scope() as session:
        for item in matches:
            source_id = str(item.get("id") or "")
            if not source_id:
                continue
            existing = session.scalar(select(Match).where(Match.source == source, Match.source_id == source_id))
            payload = {
                "source": source,
                "source_id": source_id,
                "competition_key": competition_key,
                "season": season,
                "stage": str(item.get("stage") or ""),
                "group_name": str(item.get("group") or ""),
                "matchday": str(item.get("matchday") or ""),
                "kickoff_time": parse_match_datetime(item.get("date", ""), item.get("time", "")),
                "home_team_code": str(item.get("home") or ""),
                "home_team_name": str(item.get("homeFull") or item.get("homeName") or item.get("home") or ""),
                "away_team_code": str(item.get("away") or ""),
                "away_team_name": str(item.get("awayFull") or item.get("awayName") or item.get("away") or ""),
                "status": str(item.get("status") or "upcoming"),
                "score_home": item.get("scoreH"),
                "score_away": item.get("scoreW"),
                "venue": str(item.get("venue") or ""),
                "city": str(item.get("city") or ""),
                "raw": item,
            }
            if existing:
                if payload.get("raw"):
                    merged_raw = dict(existing.raw or {})
                    merged_raw.update(payload["raw"])
                    for preserve_key in (
                        "fixtureId",
                        "fixture_id",
                        "fixtureSource",
                        "fixtureSeason",
                        "apiFootballFixture",
                        "sportsdbEventId",
                        "idEvent",
                        "sportsdbSeason",
                        "theSportsDBEvent",
                    ):
                        if preserve_key in (existing.raw or {}) and not payload["raw"].get(preserve_key):
                            merged_raw[preserve_key] = existing.raw[preserve_key]
                    payload["raw"] = merged_raw
                for key, value in payload.items():
                    if key in {"venue", "city", "home_team_name", "away_team_name"} and not value:
                        continue
                    setattr(existing, key, value)
            else:
                session.add(Match(**payload))
            count += 1
    return count


def find_match_id(session, match: dict[str, Any], competition_key: str) -> int | None:
    source_id = str(match.get("id") or "")
    if source_id:
        row = session.scalar(
            select(Match).where(Match.competition_key == competition_key, Match.source_id == source_id)
        )
        if row:
            return row.id

    date = match.get("date", "")
    home = str(match.get("homeFull") or match.get("homeName") or match.get("home") or "").lower()
    away = str(match.get("awayFull") or match.get("awayName") or match.get("away") or "").lower()
    rows = session.scalars(select(Match).where(Match.competition_key == competition_key)).all()
    for row in rows:
        row_date = row.kickoff_time.astimezone(timezone.utc).date().isoformat()
        if row_date != date:
            continue
        if home in row.home_team_name.lower() and away in row.away_team_name.lower():
            return row.id
    return None


def persist_odds_snapshots(matches: list[dict[str, Any]], competition_key: str) -> int:
    inserted = 0
    with session_scope() as session:
        for match in matches:
            odds = match.get("odds") or {}
            if not odds:
                continue
            match_id = find_match_id(session, match, competition_key)
            if match_id is None:
                continue
            source = odds.get("source", "")
            bookmaker = odds.get("bookmaker", "")
            captured_at = datetime.now(timezone.utc)
            # Check existing snapshots for this match
            existing = session.scalars(
                select(OddsSnapshot).where(
                    OddsSnapshot.match_id == match_id,
                    OddsSnapshot.competition_key == competition_key,
                )
            ).all()
            existing_set = {(s.market, s.selection, s.price) for s in existing}

            h2h = odds.get("h2h") or {}
            for selection, price in h2h.items():
                if price is None:
                    continue
                key = ("h2h", selection, float(price))
                if key in existing_set:
                    continue
                session.add(OddsSnapshot(
                    match_id=match_id,
                    competition_key=competition_key,
                    source=source,
                    bookmaker=bookmaker,
                    market="h2h",
                    selection=selection,
                    price=float(price),
                    captured_at=captured_at,
                    raw=odds,
                ))
                inserted += 1
            for item in odds.get("totals") or []:
                price = item.get("price")
                if price is None:
                    continue
                key = ("totals", str(item.get("name") or ""), float(price))
                if key in existing_set:
                    continue
                session.add(OddsSnapshot(
                    match_id=match_id,
                    competition_key=competition_key,
                    source=source,
                    bookmaker=bookmaker,
                    market="totals",
                    selection=str(item.get("name") or ""),
                    price=float(price),
                    point=item.get("point"),
                    captured_at=captured_at,
                    raw=odds,
                ))
                inserted += 1
            for item in odds.get("spreads") or []:
                point = item.get("point")
                for selection in ("home", "draw", "away"):
                    price = item.get(selection)
                    if price is None:
                        continue
                    key = ("spread", selection, float(price))
                    if key in existing_set:
                        continue
                    session.add(OddsSnapshot(
                        match_id=match_id,
                        competition_key=competition_key,
                        source=source,
                        bookmaker=bookmaker,
                        market="spread",
                        selection=selection,
                        price=float(price),
                        point=point,
                        captured_at=captured_at,
                        raw=odds,
                    ))
                    inserted += 1
    return inserted


def persist_analysis_files() -> int:
    payloads = []
    recommendations = load_json("data/recommendations.json", {})
    if recommendations:
        payloads.append(("recommendations", recommendations, "data/recommendations.json"))
    final_payload = load_json("data/final_recommendations.json", {})
    if final_payload:
        payloads.append(("final", final_payload, "data/final_recommendations.json"))

    inserted = 0
    with session_scope() as session:
        for status, payload, source in payloads:
            existing = session.scalar(
                select(AnalysisResult).where(
                    AnalysisResult.status == status,
                    AnalysisResult.model == source,
                )
            )
            if existing:
                existing.payload = payload
                existing.created_at = datetime.now(timezone.utc)
            else:
                session.add(AnalysisResult(status=status, model=source, payload=payload))
                inserted += 1
    return inserted
