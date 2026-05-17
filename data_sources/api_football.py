from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta

import requests
from sqlalchemy import select

from db import Match, MatchData, session_scope


API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE = "https://v3.football.api-sports.io"
SYNC_LIMIT = max(0, int(os.environ.get("MATCH_DATA_SYNC_LIMIT", "12")))
LINEUP_HOURS = max(1, int(os.environ.get("SYNC_LINEUP_HOURS", "4")))
CACHE = {}


def league_config(league_key):
    configs = {
        "epl": {
            "api_football_league": os.environ.get("API_FOOTBALL_EPL_LEAGUE", "39"),
            "api_football_season": os.environ.get("API_FOOTBALL_EPL_SEASON", "2025"),
        },
        "worldcup": {
            "api_football_league": os.environ.get("API_FOOTBALL_WORLDCUP_LEAGUE", "1"),
            "api_football_season": os.environ.get("API_FOOTBALL_WORLDCUP_SEASON", "2026"),
        },
    }
    return configs.get(league_key, {})


def canonical_team(value):
    text = str(value or "").lower()
    for token in [" football club", " fc", " afc", " cf", ".", "&"]:
        text = text.replace(token, " ")
    text = text.replace(" and ", " ")
    return " ".join(text.split())


def match_date_key(value):
    return str(value or "")[:10]


def odds_team_key(home, away, date):
    teams = sorted([canonical_team(home), canonical_team(away)])
    return f"{match_date_key(date)}|{teams[0]}|{teams[1]}"


def decimal_or_none(value):
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_with_cache(key, url, ttl=60):
    if not API_KEY:
        return None
    now = time.time()
    if key in CACHE and now - CACHE[key]["ts"] < ttl:
        return CACHE[key]["data"]
    try:
        resp = requests.get(url, headers={"x-apisports-key": API_KEY}, timeout=15)
        if not resp.ok:
            print(f"[API-Football] {url} -> {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        CACHE[key] = {"data": data, "ts": now}
        return data
    except Exception as exc:
        print(f"[API-Football Error] {exc}")
        return None


def fetch_fixtures(league_key):
    cfg = league_config(league_key)
    league = cfg.get("api_football_league")
    season = cfg.get("api_football_season")
    if not API_KEY or not league or not season:
        return {}
    url = f"{BASE}/fixtures?league={league}&season={season}"
    data = fetch_with_cache(f"api_football_fixtures_{league}_{season}", url, ttl=3600)
    if not isinstance(data, dict):
        return {}

    fixtures = {}
    for item in data.get("response", []):
        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        home = (teams.get("home") or {}).get("name", "")
        away = (teams.get("away") or {}).get("name", "")
        date = match_date_key(fixture.get("date", ""))
        if home and away and date:
            fixtures[odds_team_key(home, away, date)] = item
    return fixtures


def fetch_fixture_detail(fixture_id, data_type):
    if not API_KEY or not fixture_id:
        return None
    endpoint = {
        "events": "fixtures/events",
        "statistics": "fixtures/statistics",
        "lineups": "fixtures/lineups",
    }.get(data_type)
    if not endpoint:
        return None
    url = f"{BASE}/{endpoint}?fixture={fixture_id}"
    data = fetch_with_cache(f"api_football_{data_type}_{fixture_id}", url, ttl=1800)
    return data if isinstance(data, dict) else None


def fetch_odds(league_key):
    cfg = league_config(league_key)
    league = cfg.get("api_football_league")
    season = cfg.get("api_football_season")
    if not API_KEY or not league or not season:
        return {}
    url = f"{BASE}/odds?league={league}&season={season}"
    data = fetch_with_cache(f"api_football_odds_{league}_{season}", url, ttl=600)
    if not isinstance(data, dict):
        return {}

    odds_by_match = {}
    for item in data.get("response", []):
        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        home = (teams.get("home") or {}).get("name", "")
        away = (teams.get("away") or {}).get("name", "")
        date = match_date_key(fixture.get("date", ""))
        if not home or not away or not date:
            continue
        bookmaker = (item.get("bookmakers") or [{}])[0]
        match_odds = {"source": "api-football", "bookmaker": bookmaker.get("name", ""), "updated": item.get("update", ""), "h2h": {}, "totals": []}
        for bet in bookmaker.get("bets", []):
            bet_name = str(bet.get("name", "")).lower()
            if bet_name in {"match winner", "1x2"}:
                for value in bet.get("values", []):
                    label = str(value.get("value", "")).lower()
                    price = decimal_or_none(value.get("odd"))
                    if label in {"home", "1"}:
                        match_odds["h2h"]["home"] = price
                    elif label in {"draw", "x"}:
                        match_odds["h2h"]["draw"] = price
                    elif label in {"away", "2"}:
                        match_odds["h2h"]["away"] = price
            elif "goals over/under" in bet_name or "over/under" in bet_name:
                for value in bet.get("values", []):
                    raw = str(value.get("value", ""))
                    price = decimal_or_none(value.get("odd"))
                    if price is not None:
                        parts = raw.split()
                        match_odds["totals"].append({"name": parts[0] if parts else raw, "point": decimal_or_none(parts[-1] if parts else None), "price": price})
        odds_by_match[odds_team_key(home, away, date)] = match_odds
    return odds_by_match


def status_from_short(short_status):
    mapping = {
        "TBD": "upcoming", "NS": "upcoming",
        "1H": "live", "HT": "ht", "2H": "live", "ET": "live", "BT": "live", "P": "live",
        "FT": "finished", "AET": "finished", "PEN": "finished",
        "PST": "postponed", "CANC": "cancelled", "ABD": "cancelled", "AWD": "finished", "WO": "finished",
    }
    return mapping.get(str(short_status or "").upper(), "")


def fixture_key_for_match(match):
    kickoff = match.kickoff_time
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return odds_team_key(match.home_team_name or match.home_team_code, match.away_team_name or match.away_team_code, kickoff.date().isoformat())


def resolve_fixture(match):
    raw = match.raw or {}
    fixture_id = raw.get("fixtureId") or raw.get("fixture_id") or ((raw.get("fixture") or {}).get("id") if isinstance(raw.get("fixture"), dict) else None)
    if fixture_id:
        return fixture_id, None
    item = fetch_fixtures(match.competition_key).get(fixture_key_for_match(match))
    if not item:
        return None, None
    return (item.get("fixture") or {}).get("id"), item


def payload_has_content(payload):
    if not isinstance(payload, dict):
        return bool(payload)
    value = payload.get("response")
    return bool(value)


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
    row = session.scalar(select(MatchData).where(MatchData.match_id == match_id, MatchData.data_type == data_type, MatchData.source == "api-football"))
    if row:
        row.payload = payload
        row.fetched_at = datetime.now(timezone.utc)
    else:
        session.add(MatchData(match_id=match_id, data_type=data_type, source="api-football", payload=payload))


def sync_match_data(limit=None):
    if not API_KEY:
        return {"enabled": False, "synced": 0, "reason": "API_FOOTBALL_KEY not configured"}
    limit = SYNC_LIMIT if limit is None else int(limit)
    if limit <= 0:
        return {"enabled": True, "synced": 0, "reason": "MATCH_DATA_SYNC_LIMIT is 0"}
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=14)
    end = now + timedelta(days=7)
    synced = 0
    skipped = 0
    errors = []
    with session_scope() as session:
        rows = session.scalars(select(Match).where(Match.kickoff_time >= start, Match.kickoff_time <= end).order_by(Match.kickoff_time.desc()).limit(120)).all()
        for match in rows:
            if synced >= limit:
                break
            fixture_id, fixture_item = resolve_fixture(match)
            if not fixture_id:
                skipped += 1
                continue
            if fixture_item:
                raw = dict(match.raw or {})
                raw.update({
                    "fixtureId": fixture_id,
                    "fixtureSource": "api-football",
                    "fixtureSeason": league_config(match.competition_key).get("api_football_season", ""),
                    "apiFootballFixture": fixture_item,
                })
                match.raw = raw
            existing_by_type = {item.data_type: item for item in session.scalars(select(MatchData).where(MatchData.match_id == match.id, MatchData.source == "api-football")).all()}
            for data_type in ("events", "statistics", "lineups"):
                if synced >= limit:
                    break
                if not should_refresh(match, existing_by_type.get(data_type)):
                    continue
                try:
                    payload = fetch_fixture_detail(fixture_id, data_type)
                    if not payload:
                        skipped += 1
                        continue
                    upsert_match_data(session, match.id, data_type, payload)
                    synced += 1
                except Exception as exc:
                    errors.append(f"{match.source_id}:{data_type}:{exc}")
    return {"enabled": True, "synced": synced, "skipped": skipped, "errors": errors[:5]}
