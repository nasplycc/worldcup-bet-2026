from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta

import requests
from sqlalchemy import select

from db import Match, MatchData, session_scope


API_KEY = os.environ.get("THE_SPORTSDB_KEY", "123")
BASE = os.environ.get("THE_SPORTSDB_BASE", "https://www.thesportsdb.com/api/v1/json").rstrip("/")
SYNC_LIMIT = max(0, int(os.environ.get("THE_SPORTSDB_SYNC_LIMIT", "8")))
LINEUP_HOURS = max(1, int(os.environ.get("SYNC_LINEUP_HOURS", "4")))
CACHE = {}


def config(league_key):
    configs = {
        "epl": {"league_id": os.environ.get("THE_SPORTSDB_EPL_LEAGUE_ID", "4328"), "season": os.environ.get("THE_SPORTSDB_EPL_SEASON", "2025-2026")},
        "worldcup": {"league_id": os.environ.get("THE_SPORTSDB_WORLDCUP_LEAGUE_ID", ""), "season": os.environ.get("THE_SPORTSDB_WORLDCUP_SEASON", "2026")},
    }
    return configs.get(league_key, {})


def canonical_team(value):
    text = str(value or "").lower()
    for token in [" football club", " fc", " afc", " cf", ".", "&"]:
        text = text.replace(token, " ")
    text = text.replace(" and ", " ")
    return " ".join(text.split())


def odds_team_key(home, away, date):
    teams = sorted([canonical_team(home), canonical_team(away)])
    return f"{str(date or '')[:10]}|{teams[0]}|{teams[1]}"


def fetch_with_cache(key, path, ttl=3600):
    if not API_KEY:
        return None
    now = time.time()
    if key in CACHE and now - CACHE[key]["ts"] < ttl:
        return CACHE[key]["data"]
    url = f"{BASE}/{API_KEY}/{path.lstrip('/')}"
    try:
        resp = requests.get(url, timeout=15)
        if not resp.ok:
            print(f"[TheSportsDB] {path} -> {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        CACHE[key] = {"data": data, "ts": now}
        return data
    except Exception as exc:
        print(f"[TheSportsDB Error] {exc}")
        return None


def fetch_events(league_key):
    cfg = config(league_key)
    league_id = cfg.get("league_id")
    season = cfg.get("season")
    if not league_id or not season:
        return {}
    path = f"eventsseason.php?id={league_id}&s={season}"
    data = fetch_with_cache(f"thesportsdb_events_{league_id}_{season}", path, ttl=3600)
    if not isinstance(data, dict):
        return {}
    events = {}
    for item in data.get("events") or []:
        home = item.get("strHomeTeam", "")
        away = item.get("strAwayTeam", "")
        date = item.get("dateEvent", "")
        if home and away and date:
            events[odds_team_key(home, away, date)] = item
    return events


def resolve_event(match):
    raw = match.raw or {}
    event_id = raw.get("sportsdbEventId") or raw.get("idEvent")
    if event_id:
        return event_id, None
    events = fetch_events(match.competition_key)
    if not events:
        return None, None
    kickoff = match.kickoff_time
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    key = odds_team_key(match.home_team_name or match.home_team_code, match.away_team_name or match.away_team_code, kickoff.date().isoformat())
    item = events.get(key)
    if not item:
        return None, None
    return item.get("idEvent"), item


def fetch_event_detail(event_id, data_type):
    if not event_id:
        return None
    endpoint = {
        "events": f"lookuptimeline.php?id={event_id}",
        "statistics": f"lookupeventstats.php?id={event_id}",
        "lineups": f"lookuplineup.php?id={event_id}",
    }.get(data_type)
    if not endpoint:
        return None
    return fetch_with_cache(f"thesportsdb_{data_type}_{event_id}", endpoint, ttl=1800)


def payload_has_content(payload):
    if not isinstance(payload, dict):
        return bool(payload)
    for key in ("timeline", "eventstats", "lineup"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def should_refresh(match, existing_row):
    if not existing_row or match.status == "live":
        return True
    if not payload_has_content(existing_row.payload or {}):
        return True
    kickoff = match.kickoff_time
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return now <= kickoff <= now + timedelta(hours=LINEUP_HOURS)


def upsert_match_data(session, match_id, data_type, payload):
    row = session.scalar(select(MatchData).where(MatchData.match_id == match_id, MatchData.data_type == data_type, MatchData.source == "thesportsdb"))
    if row:
        row.payload = payload
        row.fetched_at = datetime.now(timezone.utc)
    else:
        session.add(MatchData(match_id=match_id, data_type=data_type, source="thesportsdb", payload=payload))


def sync_match_data(limit=None):
    limit = SYNC_LIMIT if limit is None else int(limit)
    if not API_KEY:
        return {"enabled": False, "synced": 0, "reason": "THE_SPORTSDB_KEY not configured"}
    if limit <= 0:
        return {"enabled": True, "synced": 0, "reason": "THE_SPORTSDB_SYNC_LIMIT is 0"}
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=30)
    end = now + timedelta(days=7)
    synced = 0
    skipped = 0
    errors = []
    with session_scope() as session:
        rows = session.scalars(select(Match).where(Match.kickoff_time >= start, Match.kickoff_time <= end).order_by(Match.kickoff_time.desc()).limit(120)).all()
        for match in rows:
            if synced >= limit:
                break
            event_id, event_item = resolve_event(match)
            if not event_id:
                skipped += 1
                continue
            if event_item:
                raw = dict(match.raw or {})
                raw.update({
                    "sportsdbEventId": event_id,
                    "fixtureSource": raw.get("fixtureSource") or "thesportsdb",
                    "sportsdbSeason": config(match.competition_key).get("season", ""),
                    "theSportsDBEvent": event_item,
                })
                match.raw = raw
                if not match.venue and event_item.get("strVenue"):
                    match.venue = event_item.get("strVenue")
                if event_item.get("intHomeScore") not in {None, ""}:
                    match.score_home = int(event_item.get("intHomeScore"))
                if event_item.get("intAwayScore") not in {None, ""}:
                    match.score_away = int(event_item.get("intAwayScore"))
            existing_by_type = {item.data_type: item for item in session.scalars(select(MatchData).where(MatchData.match_id == match.id, MatchData.source == "thesportsdb")).all()}
            for data_type in ("events", "statistics", "lineups"):
                if synced >= limit:
                    break
                if not should_refresh(match, existing_by_type.get(data_type)):
                    continue
                try:
                    payload = fetch_event_detail(event_id, data_type)
                    if not payload:
                        skipped += 1
                        continue
                    upsert_match_data(session, match.id, data_type, payload)
                    synced += 1
                except Exception as exc:
                    errors.append(f"{match.source_id}:{data_type}:{exc}")
    return {"enabled": True, "synced": synced, "skipped": skipped, "errors": errors[:5]}
