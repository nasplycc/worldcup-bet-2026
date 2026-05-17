"""
Flask backend for worldcup-bet-2026.

NAS deployment serves the frontend and World Cup analysis API from this process.
"""
from dotenv import load_dotenv
load_dotenv()
import os
import time
import jwt
import csv
import io
import threading
from urllib.parse import urlencode
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from flask import Flask, jsonify, has_request_context, request, send_from_directory
from sqlalchemy import select

from frontend_data import merge_worldcup_frontend, recommendations_to_frontend, schedule_to_frontend
from state import load_json
from db import AnalysisJob, AnalysisResult, Match, MatchData, OddsSnapshot, Subscription, SyncRun, User, UserPreference, db_counts, init_db, password_hash, seed_all, session_scope
from db_sync import persist_analysis_files, persist_odds_snapshots, upsert_matches_from_frontend
from ai_pipeline import analysis_job_stats, enqueue_analysis_jobs, run_analysis_jobs

app = Flask(__name__, static_folder="docs", static_url_path="")
ROOT = Path(__file__).resolve().parent

# Config
API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
ODDS_API_KEY = os.environ.get("THE_ODDS_API_KEY", "") or os.environ.get("ODDS_API_KEY", "")
ODDS_API_REGIONS = os.environ.get("THE_ODDS_API_REGIONS", "eu")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-change-me")
JWT_TTL_HOURS = int(os.environ.get("JWT_TTL_HOURS", "168"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
BASE = "https://api.football-data.org/v4"
OPENFOOTBALL_EPL_2025_26 = "https://openfootball.github.io/england/2025-26/1-premierleague.json"
THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
THE_SPORTSDB_KEY = os.environ.get("THE_SPORTSDB_KEY", "123")
THE_SPORTSDB_BASE = os.environ.get("THE_SPORTSDB_BASE", "https://www.thesportsdb.com/api/v1/json").rstrip("/")
SPORTTERY_ENABLED = os.environ.get("SPORTTERY_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
SPORTTERY_BASE = os.environ.get("SPORTTERY_BASE", "https://webapi.sporttery.cn/gateway").rstrip("/")
SPORTTERY_SYNC_LIMIT = max(0, int(os.environ.get("SPORTTERY_SYNC_LIMIT", "20")))
FOOTBALL_DATA_UK_ENABLED = os.environ.get("FOOTBALL_DATA_UK_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
FOOTBALL_DATA_UK_EPL_CSV = os.environ.get("FOOTBALL_DATA_UK_EPL_CSV", "https://www.football-data.co.uk/mmz4281/2526/E0.csv")
FOOTBALL_DATA_UK_SYNC_LIMIT = max(0, int(os.environ.get("FOOTBALL_DATA_UK_SYNC_LIMIT", "420")))
CACHE = {}
CACHE_TTL = 60
AUTO_SYNC_ENABLED = os.environ.get("AUTO_SYNC_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
AUTO_SYNC_INTERVAL_MINUTES = max(5, int(os.environ.get("AUTO_SYNC_INTERVAL_MINUTES", "60")))
SYNC_STARTUP_DELAY_SECONDS = max(0, int(os.environ.get("SYNC_STARTUP_DELAY_SECONDS", "20")))
MATCH_DATA_SYNC_LIMIT = max(0, int(os.environ.get("MATCH_DATA_SYNC_LIMIT", "12")))
THE_SPORTSDB_SYNC_LIMIT = max(0, int(os.environ.get("THE_SPORTSDB_SYNC_LIMIT", "8")))
SYNC_PREMATCH_HOURS = max(1, int(os.environ.get("SYNC_PREMATCH_HOURS", "48")))
SYNC_LINEUP_HOURS = max(1, int(os.environ.get("SYNC_LINEUP_HOURS", "4")))
SYNC_LIVE_LOOKBACK_HOURS = max(1, int(os.environ.get("SYNC_LIVE_LOOKBACK_HOURS", "3")))
SYNC_RECENT_FINISHED_HOURS = max(1, int(os.environ.get("SYNC_RECENT_FINISHED_HOURS", "24")))
SYNC_PREMATCH_MATCH_DATA_LIMIT = max(0, int(os.environ.get("SYNC_PREMATCH_MATCH_DATA_LIMIT", "18")))
SYNC_LIVE_MATCH_DATA_LIMIT = max(0, int(os.environ.get("SYNC_LIVE_MATCH_DATA_LIMIT", "30")))
SYNC_POSTMATCH_MATCH_DATA_LIMIT = max(0, int(os.environ.get("SYNC_POSTMATCH_MATCH_DATA_LIMIT", "24")))
SYNC_BASE_INTERVAL_MINUTES = max(5, int(os.environ.get("SYNC_BASE_INTERVAL_MINUTES", str(AUTO_SYNC_INTERVAL_MINUTES))))
SYNC_PREMATCH_INTERVAL_MINUTES = max(5, int(os.environ.get("SYNC_PREMATCH_INTERVAL_MINUTES", "30")))
SYNC_LIVE_INTERVAL_MINUTES = max(1, int(os.environ.get("SYNC_LIVE_INTERVAL_MINUTES", "5")))
SYNC_POSTMATCH_INTERVAL_MINUTES = max(5, int(os.environ.get("SYNC_POSTMATCH_INTERVAL_MINUTES", "60")))
AI_PREFILL_CONTEXT_BEFORE_QUEUE = os.environ.get("AI_PREFILL_CONTEXT_BEFORE_QUEUE", "true").lower() in {"1", "true", "yes", "on"}
SYNC_LOCK = threading.Lock()
SCHEDULER_STARTED = False


def bootstrap_database():
    """Initialize DB on startup; retry a few times to handle DB container race conditions."""
    retries, delay = 5, 3
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            seeded = seed_all()
            analysis_count = persist_analysis_files()
            return {"ok": True, "seeded": {**seeded, "analysis_files": analysis_count}, "counts": db_counts(), "error": ""}
        except Exception as exc:
            last_exc = exc
            print(f"[DB] bootstrap attempt {attempt}/{retries} failed: {exc}")
            if attempt < retries:
                time.sleep(delay)
    print(f"[DB] bootstrap failed after {retries} attempts: {last_exc}")
    return {"ok": False, "seeded": {}, "counts": {}, "error": str(last_exc)}


DB_STATUS = bootstrap_database()


def fetch_with_cache(key, url, ttl=CACHE_TTL):
    now = time.time()
    if key in CACHE and now - CACHE[key]["ts"] < ttl:
        return CACHE[key]["data"]
    try:
        headers = {"X-Auth-Token": API_KEY} if API_KEY else {}
        resp = requests.get(url, headers=headers, timeout=15)
        if not resp.ok:
            print(f"[API] {url} -> {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        CACHE[key] = {"data": data, "ts": now}
        return data
    except Exception as e:
        print(f"[API Error] {e}")
        return None


def fetch_public_with_cache(key, url, ttl=CACHE_TTL):
    now = time.time()
    if key in CACHE and now - CACHE[key]["ts"] < ttl:
        return CACHE[key]["data"]
    try:
        resp = requests.get(url, timeout=15)
        if not resp.ok:
            print(f"[API] {url.split('apiKey=')[0]}apiKey=*** -> {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        CACHE[key] = {"data": data, "ts": now}
        return data
    except Exception as e:
        print(f"[API Error] {e}")
        return None


def fetch_api_football_with_cache(key, url, ttl=CACHE_TTL):
    now = time.time()
    if key in CACHE and now - CACHE[key]["ts"] < ttl:
        return CACHE[key]["data"]
    try:
        headers = {"x-apisports-key": API_FOOTBALL_KEY}
        resp = requests.get(url, headers=headers, timeout=15)
        if not resp.ok:
            print(f"[API-Football] {url} -> {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        CACHE[key] = {"data": data, "ts": now}
        return data
    except Exception as e:
        print(f"[API-Football Error] {e}")
        return None


def fetch_api_football_fixtures(league_key):
    cfg = league_odds_config(league_key)
    league = cfg.get("api_football_league")
    season = cfg.get("api_football_season")
    if not API_FOOTBALL_KEY or not league or not season:
        return {}
    url = f"{API_FOOTBALL_BASE}/fixtures?league={league}&season={season}"
    data = fetch_api_football_with_cache(f"api_football_fixtures_{league}_{season}", url, ttl=3600)
    if not isinstance(data, dict):
        return {}

    fixtures = {}
    for item in data.get("response", []):
        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        home = (teams.get("home") or {}).get("name", "")
        away = (teams.get("away") or {}).get("name", "")
        date = match_date_key(fixture.get("date", ""))
        if not home or not away or not date:
            continue
        fixtures[odds_team_key(home, away, date)] = item
    return fixtures


def thesportsdb_config(league_key):
    configs = {
        "epl": {
            "league_id": os.environ.get("THE_SPORTSDB_EPL_LEAGUE_ID", "4328"),
            "season": os.environ.get("THE_SPORTSDB_EPL_SEASON", "2025-2026"),
        },
        "worldcup": {
            "league_id": os.environ.get("THE_SPORTSDB_WORLDCUP_LEAGUE_ID", ""),
            "season": os.environ.get("THE_SPORTSDB_WORLDCUP_SEASON", "2026"),
        },
    }
    return configs.get(league_key, {})


def fetch_thesportsdb_with_cache(key, path, ttl=3600):
    if not THE_SPORTSDB_KEY:
        return None
    now = time.time()
    if key in CACHE and now - CACHE[key]["ts"] < ttl:
        return CACHE[key]["data"]
    url = f"{THE_SPORTSDB_BASE}/{THE_SPORTSDB_KEY}/{path.lstrip('/')}"
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


def fetch_thesportsdb_events(league_key):
    cfg = thesportsdb_config(league_key)
    league_id = cfg.get("league_id")
    season = cfg.get("season")
    if not league_id or not season:
        return {}
    path = f"eventsseason.php?id={league_id}&s={season}"
    data = fetch_thesportsdb_with_cache(f"thesportsdb_events_{league_id}_{season}", path, ttl=3600)
    if not isinstance(data, dict):
        return {}
    events = {}
    for item in data.get("events") or []:
        home = item.get("strHomeTeam", "")
        away = item.get("strAwayTeam", "")
        date = item.get("dateEvent", "")
        if not home or not away or not date:
            continue
        events[odds_team_key(home, away, date)] = item
    return events


def fetch_sporttery_with_cache(key, path, ttl=300):
    if not SPORTTERY_ENABLED:
        return None
    now = time.time()
    if key in CACHE and now - CACHE[key]["ts"] < ttl:
        return CACHE[key]["data"]
    url = f"{SPORTTERY_BASE}/{path.lstrip('/')}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.sporttery.cn/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if not resp.ok:
            print(f"[Sporttery] {path} -> {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        if not data.get("success"):
            print(f"[Sporttery] {path} -> {data.get('errorCode')}: {data.get('errorMessage')}")
            return None
        CACHE[key] = {"data": data, "ts": now}
        return data
    except Exception as exc:
        print(f"[Sporttery Error] {exc}")
        return None


def sporttery_league_names(league_key):
    mapping = {
        "epl": {"英超", "英格兰超级联赛", "英格兰超级"},
        "worldcup": {"世界杯", "世俱杯", "FIFA世界杯"},
    }
    return mapping.get(league_key, set())


def sporttery_team_alias(name):
    mapping = {
        "阿森纳": "arsenal",
        "维拉": "aston villa",
        "阿斯顿维拉": "aston villa",
        "伯恩茅斯": "bournemouth",
        "布伦特": "brentford",
        "布伦特福德": "brentford",
        "布赖顿": "brighton hove albion",
        "布莱顿": "brighton hove albion",
        "伯恩利": "burnley",
        "切尔西": "chelsea",
        "水晶宫": "crystal palace",
        "埃弗顿": "everton",
        "富勒姆": "fulham",
        "利兹联": "leeds united",
        "利物浦": "liverpool",
        "曼城": "manchester city",
        "曼联": "manchester united",
        "曼彻斯特联": "manchester united",
        "曼彻斯特城": "manchester city",
        "纽卡斯尔": "newcastle united",
        "纽卡": "newcastle united",
        "纽卡斯尔联": "newcastle united",
        "诺丁汉": "nottingham forest",
        "诺丁汉森林": "nottingham forest",
        "桑德兰": "sunderland",
        "热刺": "tottenham hotspur",
        "西汉姆": "west ham united",
        "西汉姆联": "west ham united",
        "狼队": "wolverhampton wanderers",
    }
    text = str(name or "").strip()
    return mapping.get(text, canonical_team(text))


def sporttery_team_key(home, away, date):
    teams = sorted([sporttery_team_alias(home), sporttery_team_alias(away)])
    return f"{match_date_key(date)}|{teams[0]}|{teams[1]}"


def football_data_uk_team_alias(name):
    mapping = {
        "Arsenal": "arsenal",
        "Aston Villa": "aston villa",
        "Bournemouth": "bournemouth",
        "Brentford": "brentford",
        "Brighton": "brighton hove albion",
        "Burnley": "burnley",
        "Chelsea": "chelsea",
        "Crystal Palace": "crystal palace",
        "Everton": "everton",
        "Fulham": "fulham",
        "Leeds": "leeds united",
        "Liverpool": "liverpool",
        "Man City": "manchester city",
        "Man United": "manchester united",
        "Newcastle": "newcastle united",
        "Nott'm Forest": "nottingham forest",
        "Sunderland": "sunderland",
        "Tottenham": "tottenham hotspur",
        "West Ham": "west ham united",
        "Wolves": "wolverhampton wanderers",
    }
    return mapping.get(str(name or "").strip(), canonical_team(name))


def football_data_uk_key(home, away, date):
    teams = sorted([football_data_uk_team_alias(home), football_data_uk_team_alias(away)])
    return f"{match_date_key(date)}|{teams[0]}|{teams[1]}"


def football_data_uk_ordered_key(home, away):
    return f"{football_data_uk_team_alias(home)}|{football_data_uk_team_alias(away)}"


def sporttery_datetime(item):
    date = item.get("matchDate") or item.get("businessDate") or ""
    time_text = item.get("matchTime") or "00:00"
    try:
        return datetime.fromisoformat(f"{date}T{time_text}:00+08:00")
    except Exception:
        return None


def sporttery_captured_at(pool):
    date = pool.get("updateDate") or ""
    time_text = pool.get("updateTime") or ""
    if date and time_text:
        try:
            return datetime.fromisoformat(f"{date}T{time_text}+08:00").astimezone(timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def sporttery_price(value):
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def flatten_sporttery_matches(data):
    rows = []
    value = data.get("value") or {}
    for group in value.get("matchInfoList") or []:
        for item in group.get("subMatchList") or []:
            if item.get("isHide"):
                continue
            rows.append(item)
    return rows


def fetch_sporttery_matches():
    path = "jc/football/getMatchCalculatorV1.qry?poolCode=hhad,had&channel=c"
    data = fetch_sporttery_with_cache("sporttery_match_calculator", path, ttl=300)
    if not isinstance(data, dict):
        return []
    return flatten_sporttery_matches(data)


def sporttery_odds_from_item(item):
    had = item.get("had") or {}
    hhad = item.get("hhad") or {}
    h2h = {
        "home": sporttery_price(had.get("h")),
        "draw": sporttery_price(had.get("d")),
        "away": sporttery_price(had.get("a")),
    }
    h2h = {key: value for key, value in h2h.items() if value is not None}
    spread = {
        "point": sporttery_price(hhad.get("goalLineValue") or hhad.get("goalLine")),
        "home": sporttery_price(hhad.get("h")),
        "draw": sporttery_price(hhad.get("d")),
        "away": sporttery_price(hhad.get("a")),
    }
    if not any(value is not None for key, value in spread.items() if key != "point"):
        spread = {}
    updated = ""
    if had.get("updateDate") and had.get("updateTime"):
        updated = f"{had.get('updateDate')}T{had.get('updateTime')}+08:00"
    elif hhad.get("updateDate") and hhad.get("updateTime"):
        updated = f"{hhad.get('updateDate')}T{hhad.get('updateTime')}+08:00"
    return {
        "source": "sporttery",
        "bookmaker": "Sporttery",
        "updated": updated,
        "matchNum": item.get("matchNumStr") or item.get("matchNum"),
        "league": item.get("leagueAbbName") or item.get("leagueAllName") or "",
        "h2h": h2h,
        "spreads": [spread] if spread else [],
        "raw": item,
    }


def fetch_sporttery_odds(league_key):
    names = sporttery_league_names(league_key)
    if not names:
        return {}
    odds_by_match = {}
    for item in fetch_sporttery_matches():
        league = str(item.get("leagueAbbName") or item.get("leagueAllName") or "")
        if league not in names:
            continue
        home = item.get("homeTeamAllName") or item.get("homeTeamAbbName") or ""
        away = item.get("awayTeamAllName") or item.get("awayTeamAbbName") or ""
        date = item.get("matchDate") or item.get("businessDate") or ""
        if not home or not away or not date:
            continue
        odds_by_match[sporttery_team_key(home, away, date)] = sporttery_odds_from_item(item)
    return odds_by_match


def api_football_fixture_key_for_match(match):
    kickoff = match.kickoff_time
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return odds_team_key(
        match.home_team_name or match.home_team_code,
        match.away_team_name or match.away_team_code,
        kickoff.date().isoformat(),
    )


def resolve_api_football_fixture(match):
    raw = match.raw or {}
    fixture_id = (
        raw.get("fixtureId")
        or raw.get("fixture_id")
        or ((raw.get("fixture") or {}).get("id") if isinstance(raw.get("fixture"), dict) else None)
    )
    if fixture_id:
        return fixture_id, None
    fixtures = fetch_api_football_fixtures(match.competition_key)
    if not fixtures:
        return None, None
    item = fixtures.get(api_football_fixture_key_for_match(match))
    if not item:
        return None, None
    fixture = item.get("fixture") or {}
    return fixture.get("id"), item


def resolve_thesportsdb_event(match):
    raw = match.raw or {}
    event_id = raw.get("sportsdbEventId") or raw.get("idEvent")
    if event_id:
        return event_id, None
    events = fetch_thesportsdb_events(match.competition_key)
    if not events:
        return None, None
    kickoff = match.kickoff_time
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    key = odds_team_key(
        match.home_team_name or match.home_team_code,
        match.away_team_name or match.away_team_code,
        kickoff.date().isoformat(),
    )
    item = events.get(key)
    if not item:
        return None, None
    return item.get("idEvent"), item


def fetch_thesportsdb_event_detail(event_id, data_type):
    if not event_id:
        return None
    endpoint = {
        "events": f"lookuptimeline.php?id={event_id}",
        "statistics": f"lookupeventstats.php?id={event_id}",
        "lineups": f"lookuplineup.php?id={event_id}",
    }.get(data_type)
    if not endpoint:
        return None
    return fetch_thesportsdb_with_cache(f"thesportsdb_{data_type}_{event_id}", endpoint, ttl=1800)


def fetch_api_football_fixture_detail(fixture_id, data_type):
    if not API_FOOTBALL_KEY or not fixture_id:
        return None
    endpoint = {
        "events": "fixtures/events",
        "statistics": "fixtures/statistics",
        "lineups": "fixtures/lineups",
    }.get(data_type)
    if not endpoint:
        return None
    url = f"{API_FOOTBALL_BASE}/{endpoint}?fixture={fixture_id}"
    data = fetch_api_football_with_cache(f"api_football_{data_type}_{fixture_id}", url, ttl=1800)
    return data if isinstance(data, dict) else None


def upsert_match_data(session, match_id, data_type, payload, source="api-football"):
    row = session.scalar(
        select(MatchData).where(
            MatchData.match_id == match_id,
            MatchData.data_type == data_type,
            MatchData.source == source,
        )
    )
    if row:
        row.payload = payload
        row.fetched_at = datetime.now(timezone.utc)
    else:
        session.add(MatchData(match_id=match_id, data_type=data_type, source=source, payload=payload))


def match_data_has_content(payload):
    if not isinstance(payload, dict):
        return bool(payload)
    for key in ("response", "events", "eventstats", "lineup", "timeline"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return True
        if isinstance(value, dict) and value:
            return True
    if payload.get("fixture") or payload.get("odds"):
        return True
    return False


def should_refresh_match_data(match, existing_row):
    if not existing_row:
        return True
    if match.status == "live":
        return True
    if not match_data_has_content(existing_row.payload or {}):
        return True
    kickoff = match.kickoff_time
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return now <= kickoff <= now + timedelta(hours=SYNC_LINEUP_HOURS)


def sync_api_football_match_data(limit=None):
    if not API_FOOTBALL_KEY:
        return {"enabled": False, "synced": 0, "reason": "API_FOOTBALL_KEY not configured"}
    limit = MATCH_DATA_SYNC_LIMIT if limit is None else int(limit)
    if limit <= 0:
        return {"enabled": True, "synced": 0, "reason": "MATCH_DATA_SYNC_LIMIT is 0"}
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=14)
    end = now + timedelta(days=7)
    synced = 0
    skipped = 0
    errors = []
    with session_scope() as session:
        rows = session.scalars(
            select(Match)
            .where(Match.kickoff_time >= start, Match.kickoff_time <= end)
            .order_by(Match.kickoff_time.desc())
            .limit(120)
        ).all()
        for match in rows:
            if synced >= limit:
                break
            fixture_id, fixture_item = resolve_api_football_fixture(match)
            if not fixture_id:
                skipped += 1
                continue
            if fixture_item:
                raw = dict(match.raw or {})
                raw.update({
                    "fixtureId": fixture_id,
                    "fixtureSource": "api-football",
                    "fixtureSeason": league_odds_config(match.competition_key).get("api_football_season", ""),
                    "apiFootballFixture": fixture_item,
                })
                match.raw = raw
            existing_by_type = {
                item.data_type: item
                for item in session.scalars(
                    select(MatchData).where(MatchData.match_id == match.id, MatchData.source == "api-football")
                ).all()
            }
            for data_type in ("events", "statistics", "lineups"):
                if synced >= limit:
                    break
                if not should_refresh_match_data(match, existing_by_type.get(data_type)):
                    continue
                try:
                    payload = fetch_api_football_fixture_detail(fixture_id, data_type)
                    if not payload:
                        skipped += 1
                        continue
                    upsert_match_data(session, match.id, data_type, payload)
                    synced += 1
                except Exception as exc:
                    errors.append(f"{match.source_id}:{data_type}:{exc}")
    return {"enabled": True, "synced": synced, "skipped": skipped, "errors": errors[:5]}


def sync_thesportsdb_match_data(limit=None):
    limit = THE_SPORTSDB_SYNC_LIMIT if limit is None else int(limit)
    if not THE_SPORTSDB_KEY:
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
        rows = session.scalars(
            select(Match)
            .where(Match.kickoff_time >= start, Match.kickoff_time <= end)
            .order_by(Match.kickoff_time.desc())
            .limit(120)
        ).all()
        for match in rows:
            if synced >= limit:
                break
            event_id, event_item = resolve_thesportsdb_event(match)
            if not event_id:
                skipped += 1
                continue
            if event_item:
                raw = dict(match.raw or {})
                raw.update({
                    "sportsdbEventId": event_id,
                    "fixtureSource": raw.get("fixtureSource") or "thesportsdb",
                    "sportsdbSeason": thesportsdb_config(match.competition_key).get("season", ""),
                    "theSportsDBEvent": event_item,
                })
                match.raw = raw
                if not match.venue and event_item.get("strVenue"):
                    match.venue = event_item.get("strVenue")
                if event_item.get("intHomeScore") not in {None, ""}:
                    match.score_home = int(event_item.get("intHomeScore"))
                if event_item.get("intAwayScore") not in {None, ""}:
                    match.score_away = int(event_item.get("intAwayScore"))
            existing_by_type = {
                item.data_type: item for item in session.scalars(
                    select(MatchData).where(MatchData.match_id == match.id, MatchData.source == "thesportsdb")
                ).all()
            }
            for data_type in ("events", "statistics", "lineups"):
                if synced >= limit:
                    break
                if not should_refresh_match_data(match, existing_by_type.get(data_type)):
                    continue
                try:
                    payload = fetch_thesportsdb_event_detail(event_id, data_type)
                    if not payload:
                        skipped += 1
                        continue
                    upsert_match_data(session, match.id, data_type, payload, source="thesportsdb")
                    synced += 1
                except Exception as exc:
                    errors.append(f"{match.source_id}:{data_type}:{exc}")
    return {"enabled": True, "synced": synced, "skipped": skipped, "errors": errors[:5]}


def add_sporttery_snapshots(session, match, odds):
    existing = session.scalars(
        select(OddsSnapshot).where(
            OddsSnapshot.match_id == match.id,
            OddsSnapshot.competition_key == match.competition_key,
            OddsSnapshot.source == "sporttery",
        )
    ).all()
    existing_set = {
        (row.market, row.selection, row.point, round(float(row.price), 4))
        for row in existing
    }
    inserted = 0
    raw = odds.get("raw") or {}
    had = raw.get("had") or {}
    hhad = raw.get("hhad") or {}
    for selection, price in (odds.get("h2h") or {}).items():
        key = ("h2h", selection, None, round(float(price), 4))
        if key in existing_set:
            continue
        session.add(OddsSnapshot(
            match_id=match.id,
            competition_key=match.competition_key,
            source="sporttery",
            bookmaker="Sporttery",
            market="h2h",
            selection=selection,
            price=float(price),
            captured_at=sporttery_captured_at(had),
            raw=odds,
        ))
        inserted += 1
    for spread in odds.get("spreads") or []:
        point = spread.get("point")
        for selection in ("home", "draw", "away"):
            price = spread.get(selection)
            if price is None:
                continue
            key = ("spread", selection, point, round(float(price), 4))
            if key in existing_set:
                continue
            session.add(OddsSnapshot(
                match_id=match.id,
                competition_key=match.competition_key,
                source="sporttery",
                bookmaker="Sporttery",
                market="spread",
                selection=selection,
                price=float(price),
                point=point,
                captured_at=sporttery_captured_at(hhad),
                raw=odds,
            ))
            inserted += 1
    return inserted


def sync_sporttery_match_data(limit=None):
    if not SPORTTERY_ENABLED:
        return {"enabled": False, "synced": 0, "reason": "SPORTTERY_ENABLED is false"}
    limit = SPORTTERY_SYNC_LIMIT if limit is None else int(limit)
    if limit <= 0:
        return {"enabled": True, "synced": 0, "reason": "SPORTTERY_SYNC_LIMIT is 0"}
    items = fetch_sporttery_matches()
    if not items:
        return {"enabled": True, "synced": 0, "skipped": 0, "reason": "no public Sporttery matches returned"}

    public_by_key = {}
    for item in items:
        home = item.get("homeTeamAllName") or item.get("homeTeamAbbName") or ""
        away = item.get("awayTeamAllName") or item.get("awayTeamAbbName") or ""
        date = item.get("matchDate") or item.get("businessDate") or ""
        if home and away and date:
            public_by_key[sporttery_team_key(home, away, date)] = item

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=7)
    end = now + timedelta(days=14)
    synced = 0
    snapshots = 0
    skipped = 0
    with session_scope() as session:
        rows = session.scalars(
            select(Match)
            .where(Match.kickoff_time >= start, Match.kickoff_time <= end)
            .order_by(Match.kickoff_time.asc())
            .limit(240)
        ).all()
        for match in rows:
            if synced >= limit:
                break
            kickoff = match.kickoff_time
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
            key = sporttery_team_key(
                match.home_team_name or match.home_team_code,
                match.away_team_name or match.away_team_code,
                kickoff.astimezone(timezone(timedelta(hours=8))).date().isoformat(),
            )
            item = public_by_key.get(key)
            if not item:
                skipped += 1
                continue
            odds = sporttery_odds_from_item(item)
            raw = dict(match.raw or {})
            raw.update({
                "sportteryMatchId": item.get("matchId"),
                "sportteryMatchNum": item.get("matchNumStr") or item.get("matchNum"),
                "sportteryLeague": item.get("leagueAbbName") or item.get("leagueAllName") or "",
                "sportteryFixture": item,
            })
            match.raw = raw
            kickoff_cst = sporttery_datetime(item)
            if kickoff_cst:
                match.kickoff_time = kickoff_cst.astimezone(timezone.utc)
            if item.get("homeTeamAbbName") and not match.home_team_name:
                match.home_team_name = item.get("homeTeamAbbName")
            if item.get("awayTeamAbbName") and not match.away_team_name:
                match.away_team_name = item.get("awayTeamAbbName")
            upsert_match_data(session, match.id, "official_market", {"provider": "Sporttery", "fixture": item, "odds": odds}, source="sporttery")
            snapshots += add_sporttery_snapshots(session, match, odds)
            synced += 1
    return {"enabled": True, "synced": synced, "snapshots": snapshots, "skipped": skipped}


def fetch_football_data_uk_rows(league_key):
    if not FOOTBALL_DATA_UK_ENABLED:
        return []
    if league_key != "epl":
        return []
    now = time.time()
    cache_key = f"football_data_uk_{league_key}"
    if cache_key in CACHE and now - CACHE[cache_key]["ts"] < 3600:
        return CACHE[cache_key]["data"]
    try:
        resp = requests.get(FOOTBALL_DATA_UK_EPL_CSV, timeout=20)
        if not resp.ok:
            print(f"[football-data.co.uk] {resp.status_code}: {resp.text[:200]}")
            return []
        text = resp.content.decode("utf-8-sig", errors="replace")
        rows = [row for row in csv.DictReader(io.StringIO(text)) if row.get("Date") and row.get("HomeTeam") and row.get("AwayTeam")]
        CACHE[cache_key] = {"data": rows, "ts": now}
        return rows
    except Exception as exc:
        print(f"[football-data.co.uk Error] {exc}")
        return []


def parse_football_data_uk_date(value):
    try:
        return datetime.strptime(str(value or "").strip(), "%d/%m/%Y").date().isoformat()
    except Exception:
        return ""


def int_or_none(value):
    try:
        if value in {None, ""}:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def football_data_uk_payload(row):
    stats = {
        "full_time": {
            "home_goals": int_or_none(row.get("FTHG")),
            "away_goals": int_or_none(row.get("FTAG")),
            "result": row.get("FTR") or "",
        },
        "half_time": {
            "home_goals": int_or_none(row.get("HTHG")),
            "away_goals": int_or_none(row.get("HTAG")),
            "result": row.get("HTR") or "",
        },
        "match_stats": {
            "home_shots": int_or_none(row.get("HS")),
            "away_shots": int_or_none(row.get("AS")),
            "home_shots_on_target": int_or_none(row.get("HST")),
            "away_shots_on_target": int_or_none(row.get("AST")),
            "home_fouls": int_or_none(row.get("HF")),
            "away_fouls": int_or_none(row.get("AF")),
            "home_corners": int_or_none(row.get("HC")),
            "away_corners": int_or_none(row.get("AC")),
            "home_yellow_cards": int_or_none(row.get("HY")),
            "away_yellow_cards": int_or_none(row.get("AY")),
            "home_red_cards": int_or_none(row.get("HR")),
            "away_red_cards": int_or_none(row.get("AR")),
        },
        "referee": row.get("Referee") or "",
    }
    odds = {
        "h2h": {
            "home": decimal_or_none(row.get("AvgCH") or row.get("AvgH")),
            "draw": decimal_or_none(row.get("AvgCD") or row.get("AvgD")),
            "away": decimal_or_none(row.get("AvgCA") or row.get("AvgA")),
        },
        "totals": [
            {"name": "over", "point": 2.5, "price": decimal_or_none(row.get("AvgC>2.5") or row.get("Avg>2.5"))},
            {"name": "under", "point": 2.5, "price": decimal_or_none(row.get("AvgC<2.5") or row.get("Avg<2.5"))},
        ],
        "spreads": [{
            "point": decimal_or_none(row.get("AHCh") or row.get("AHh")),
            "home": decimal_or_none(row.get("AvgCAHH") or row.get("AvgAHH")),
            "away": decimal_or_none(row.get("AvgCAHA") or row.get("AvgAHA")),
        }],
    }
    odds["h2h"] = {key: value for key, value in odds["h2h"].items() if value is not None}
    odds["totals"] = [item for item in odds["totals"] if item["price"] is not None]
    odds["spreads"] = [
        item for item in odds["spreads"]
        if item["point"] is not None and (item["home"] is not None or item["away"] is not None)
    ]
    return {
        "provider": "football-data.co.uk",
        "competition": row.get("Div") or "E0",
        "date": parse_football_data_uk_date(row.get("Date")),
        "time": row.get("Time") or "",
        "homeTeam": row.get("HomeTeam") or "",
        "awayTeam": row.get("AwayTeam") or "",
        "stats": stats,
        "odds": odds,
        "raw": row,
    }


def add_football_data_uk_snapshots(session, match, payload):
    odds = payload.get("odds") or {}
    existing = session.scalars(
        select(OddsSnapshot).where(
            OddsSnapshot.match_id == match.id,
            OddsSnapshot.competition_key == match.competition_key,
            OddsSnapshot.source == "football-data.co.uk",
        )
    ).all()
    existing_set = {
        (row.market, row.selection, row.point, round(float(row.price), 4))
        for row in existing
    }
    inserted = 0
    captured_at = datetime.now(timezone.utc)
    for selection, price in (odds.get("h2h") or {}).items():
        key = ("h2h", selection, None, round(float(price), 4))
        if key in existing_set:
            continue
        session.add(OddsSnapshot(
            match_id=match.id,
            competition_key=match.competition_key,
            source="football-data.co.uk",
            bookmaker="Average closing",
            market="h2h",
            selection=selection,
            price=float(price),
            captured_at=captured_at,
            raw=payload,
        ))
        inserted += 1
    for item in odds.get("totals") or []:
        price = item.get("price")
        if price is None:
            continue
        key = ("totals", str(item.get("name") or ""), item.get("point"), round(float(price), 4))
        if key in existing_set:
            continue
        session.add(OddsSnapshot(
            match_id=match.id,
            competition_key=match.competition_key,
            source="football-data.co.uk",
            bookmaker="Average closing",
            market="totals",
            selection=str(item.get("name") or ""),
            point=item.get("point"),
            price=float(price),
            captured_at=captured_at,
            raw=payload,
        ))
        inserted += 1
    for item in odds.get("spreads") or []:
        point = item.get("point")
        for selection in ("home", "away"):
            price = item.get(selection)
            if price is None:
                continue
            key = ("spread", selection, point, round(float(price), 4))
            if key in existing_set:
                continue
            session.add(OddsSnapshot(
                match_id=match.id,
                competition_key=match.competition_key,
                source="football-data.co.uk",
                bookmaker="Average closing",
                market="spread",
                selection=selection,
                point=point,
                price=float(price),
                captured_at=captured_at,
                raw=payload,
            ))
            inserted += 1
    return inserted


def sync_football_data_uk_match_data(limit=None):
    if not FOOTBALL_DATA_UK_ENABLED:
        return {"enabled": False, "synced": 0, "reason": "FOOTBALL_DATA_UK_ENABLED is false"}
    limit = FOOTBALL_DATA_UK_SYNC_LIMIT if limit is None else int(limit)
    if limit <= 0:
        return {"enabled": True, "synced": 0, "reason": "FOOTBALL_DATA_UK_SYNC_LIMIT is 0"}
    rows = fetch_football_data_uk_rows("epl")
    if not rows:
        return {"enabled": True, "synced": 0, "skipped": 0, "reason": "no football-data.co.uk rows returned"}

    row_by_key = {}
    row_by_ordered_key = {}
    for row in rows:
        date = parse_football_data_uk_date(row.get("Date"))
        if not date:
            continue
        row_by_key[football_data_uk_key(row.get("HomeTeam"), row.get("AwayTeam"), date)] = row
        ordered_key = football_data_uk_ordered_key(row.get("HomeTeam"), row.get("AwayTeam"))
        row_by_ordered_key.setdefault(ordered_key, []).append(row)

    synced = 0
    snapshots = 0
    skipped = 0
    with session_scope() as session:
        matches = session.scalars(
            select(Match)
            .where(Match.competition_key == "epl")
            .order_by(Match.kickoff_time.desc())
            .limit(420)
        ).all()
        for match in matches:
            if synced >= limit:
                break
            kickoff = match.kickoff_time
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
            match_date = kickoff.astimezone(timezone(timedelta(hours=8))).date().isoformat()
            key = football_data_uk_key(
                match.home_team_name or match.home_team_code,
                match.away_team_name or match.away_team_code,
                match_date,
            )
            row = row_by_key.get(key)
            if not row:
                ordered_key = football_data_uk_ordered_key(
                    match.home_team_name or match.home_team_code,
                    match.away_team_name or match.away_team_code,
                )
                candidates = row_by_ordered_key.get(ordered_key) or []
                nearest = None
                nearest_days = 999
                for candidate in candidates:
                    candidate_date = parse_football_data_uk_date(candidate.get("Date"))
                    if not candidate_date:
                        continue
                    days = abs((datetime.fromisoformat(match_date) - datetime.fromisoformat(candidate_date)).days)
                    if days < nearest_days:
                        nearest = candidate
                        nearest_days = days
                if nearest is not None and nearest_days <= 10:
                    row = nearest
            if not row:
                skipped += 1
                continue
            payload = football_data_uk_payload(row)
            stats = payload.get("stats") or {}
            ft = stats.get("full_time") or {}
            if ft.get("home_goals") is not None:
                match.score_home = ft.get("home_goals")
            if ft.get("away_goals") is not None:
                match.score_away = ft.get("away_goals")
            if match.score_home is not None and match.score_away is not None:
                match.status = "finished"
            raw = dict(match.raw or {})
            raw.update({"footballDataUk": payload})
            match.raw = raw
            upsert_match_data(session, match.id, "historical_stats", payload, source="football-data.co.uk")
            snapshots += add_football_data_uk_snapshots(session, match, payload)
            synced += 1
    return {"enabled": True, "synced": synced, "snapshots": snapshots, "skipped": skipped}


@app.route("/")
def index():
    return send_from_directory("docs", "index.html")


def user_payload(user):
    subscription = get_subscription_payload(user.id) if user and getattr(user, "id", None) else {"plan": "free", "status": "active"}
    return {
        "id": user.id,
        "email": user.email,
        "displayName": user.display_name,
        "role": user.role,
        "status": user.status,
        "subscription": subscription,
    }


def get_subscription_payload(user_id):
    with session_scope() as session:
        sub = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        if not sub:
            sub = Subscription(user_id=user_id, plan="free", status="active")
            session.add(sub)
            session.flush()
        return {
            "plan": sub.plan,
            "status": sub.status,
            "startedAt": sub.started_at.isoformat() if sub.started_at else "",
            "expiresAt": sub.expires_at.isoformat() if sub.expires_at else "",
        }


def subscription_payload(sub):
    return {
        "plan": sub.plan,
        "status": sub.status,
        "startedAt": sub.started_at.isoformat() if sub.started_at else "",
        "expiresAt": sub.expires_at.isoformat() if sub.expires_at else "",
    }


def current_plan():
    user = current_user_from_request()
    if not user:
        return "free"
    sub = get_subscription_payload(user.id)
    return sub.get("plan", "free") if sub.get("status") == "active" else "free"


def is_admin_request():
    user = current_user_from_request()
    if user and user.role == "admin":
        return True
    token = request.headers.get("X-Admin-Token", "")
    return bool(ADMIN_TOKEN and token == ADMIN_TOKEN)


def issue_token(user):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_TTL_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def current_user_from_request():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user_id = int(payload.get("sub", 0))
    except Exception:
        return None
    with session_scope() as session:
        return session.get(User, user_id)


@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    display_name = str(data.get("displayName") or data.get("display_name") or "").strip()
    if not email or "@" not in email:
        return jsonify({"error": "Invalid email"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    with session_scope() as session:
        existing = session.scalar(select(User).where(User.email == email))
        if existing:
            return jsonify({"error": "Email already registered"}), 409
        user = User(email=email, password_hash=password_hash(password), display_name=display_name or email.split("@")[0])
        session.add(user)
        session.flush()
        session.add(UserPreference(user_id=user.id))
        session.add(Subscription(user_id=user.id, plan="free", status="active"))
        token = issue_token(user)
        payload = {
            "id": user.id,
            "email": user.email,
            "displayName": user.display_name,
            "role": user.role,
            "status": user.status,
            "subscription": {"plan": "free", "status": "active", "startedAt": datetime.now(timezone.utc).isoformat(), "expiresAt": ""},
        }
        return jsonify({"token": token, "user": payload}), 201


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == email))
        if not user or not user.verify_password(password):
            return jsonify({"error": "Invalid email or password"}), 401
        if user.status != "active":
            return jsonify({"error": "User is not active"}), 403
        user.last_login_at = datetime.now(timezone.utc)
        sub = session.scalar(select(Subscription).where(Subscription.user_id == user.id))
        if not sub:
            sub = Subscription(user_id=user.id, plan="free", status="active")
            session.add(sub)
            session.flush()
        token = issue_token(user)
        payload = {
            "id": user.id,
            "email": user.email,
            "displayName": user.display_name,
            "role": user.role,
            "status": user.status,
            "subscription": subscription_payload(sub),
        }
        return jsonify({"token": token, "user": payload})


@app.route("/api/auth/me")
def auth_me():
    user = current_user_from_request()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"user": user_payload(user)})


@app.route("/api/account/subscription")
def account_subscription():
    user = current_user_from_request()
    if not user:
        return jsonify({"subscription": {"plan": "free", "status": "active"}})
    return jsonify({"subscription": get_subscription_payload(user.id)})


def normalize_profile_items(value, limit=80):
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        league = str(item.get("league") or "").strip()
        match_id = str(item.get("id") or "").strip()
        key = f"{league}:{match_id}"
        if not league or not match_id or key in seen:
            continue
        seen.add(key)
        result.append({
            "league": league,
            "id": match_id,
            "title": str(item.get("title") or "")[:160],
            "sub": str(item.get("sub") or "")[:160],
            "savedAt": str(item.get("savedAt") or item.get("viewedAt") or ""),
            "viewedAt": str(item.get("viewedAt") or item.get("savedAt") or ""),
        })
        if len(result) >= limit:
            break
    return result


def preference_profile_payload(pref):
    raw = pref.watchlist if pref and isinstance(pref.watchlist, (dict, list)) else {}
    if isinstance(raw, list):
        raw = {"favorites": raw, "history": []}
    return {
        "favorites": normalize_profile_items(raw.get("favorites"), limit=120),
        "history": normalize_profile_items(raw.get("history"), limit=120),
    }


@app.route("/api/account/profile-data", methods=["GET", "POST"])
def account_profile_data():
    user = current_user_from_request()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    with session_scope() as session:
        pref = session.scalar(select(UserPreference).where(UserPreference.user_id == user.id))
        if not pref:
            pref = UserPreference(user_id=user.id)
            session.add(pref)
            session.flush()
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            current = preference_profile_payload(pref)
            payload = {
                "favorites": normalize_profile_items(data.get("favorites", current["favorites"]), limit=120),
                "history": normalize_profile_items(data.get("history", current["history"]), limit=120),
            }
            pref.watchlist = payload
            pref.updated_at = datetime.now(timezone.utc)
            session.flush()
        return jsonify({"profile": preference_profile_payload(pref)})


@app.route("/api/admin/users/<int:user_id>/subscription", methods=["POST"])
def admin_set_subscription(user_id):
    if not is_admin_request():
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    plan = str(data.get("plan") or "free").lower()
    status = str(data.get("status") or "active").lower()
    if plan not in {"free", "pro"}:
        return jsonify({"error": "plan must be free or pro"}), 400
    if status not in {"active", "paused", "cancelled"}:
        return jsonify({"error": "invalid status"}), 400
    with session_scope() as session:
        user = session.get(User, user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        sub = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        if not sub:
            sub = Subscription(user_id=user_id)
            session.add(sub)
        sub.plan = plan
        sub.status = status
        sub.updated_at = datetime.now(timezone.utc)
        session.flush()
        return jsonify({"userId": user_id, "subscription": {
            "plan": sub.plan,
            "status": sub.status,
            "startedAt": sub.started_at.isoformat() if sub.started_at else "",
            "expiresAt": sub.expires_at.isoformat() if sub.expires_at else "",
        }})


def map_status(status):
    m = {
        "SCHEDULED": "upcoming", "TIMED": "upcoming",
        "IN_PLAY": "live", "PAUSED": "ht",
        "FINISHED": "finished", "AET": "finished", "PEN": "finished",
        "POSTPONED": "postponed", "SUSPENDED": "postponed", "CANCELLED": "cancelled",
    }
    return m.get(status, "upcoming")


def map_api_football_status(short_status):
    m = {
        "TBD": "upcoming", "NS": "upcoming",
        "1H": "live", "HT": "ht", "2H": "live", "ET": "live", "BT": "live", "P": "live",
        "FT": "finished", "AET": "finished", "PEN": "finished",
        "PST": "postponed", "CANC": "cancelled", "ABD": "cancelled", "AWD": "finished", "WO": "finished",
    }
    return m.get(str(short_status or "").upper(), "")


def cst_now():
    return datetime.now(timezone(timedelta(hours=8)))


def match_status_from_kickoff(kickoff):
    now = datetime.now(timezone.utc)
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    if now < kickoff:
        return "upcoming"
    if now <= kickoff + timedelta(hours=2):
        return "live"
    return "finished"


def file_info(path):
    p = ROOT / path
    if not p.exists():
        return {"exists": False, "count": 0, "updated": ""}
    count = 0
    try:
        if p.suffix == ".json":
            data = load_json(path, [])
            if isinstance(data, list):
                count = len(data)
            elif isinstance(data, dict):
                count = len(data.get("recommendations", data.get("matches", data)))
        elif p.suffix == ".csv":
            count = max(0, len(p.read_text(encoding="utf-8-sig").splitlines()) - 1)
    except Exception:
        count = 0
    return {
        "exists": True,
        "count": count,
        "updated": datetime.fromtimestamp(p.stat().st_mtime, timezone(timedelta(hours=8))).isoformat(),
    }


def load_worldcup_frontend_payload():
    db_payload = load_worldcup_frontend_from_db()
    if db_payload:
        attach_odds(db_payload.get("matches", []), "worldcup")
        return db_payload, 200

    schedule_payload = load_json("data/worldcup_2026_schedule.json", [])
    schedule_frontend = schedule_to_frontend(schedule_payload) if schedule_payload else {}

    payload = load_json("data/recommendations.json", {})
    if payload and not is_demo_recommendation_payload(payload):
        recommendations_frontend = recommendations_to_frontend(payload)
        if schedule_frontend:
            merged = merge_worldcup_frontend(schedule_frontend, recommendations_frontend)
            attach_odds(merged.get("matches", []), "worldcup")
            return merged, 200
        attach_odds(recommendations_frontend.get("matches", []), "worldcup")
        return recommendations_frontend, 200

    if schedule_frontend:
        attach_odds(schedule_frontend.get("matches", []), "worldcup")
        return schedule_frontend, 200

    static_payload = load_json("docs/data/worldcup_matches.json", {})
    if static_payload:
        static_payload.setdefault("source", "docs/data/worldcup_matches.json")
        return static_payload, 200

    return {
        "error": "World Cup data not found. Run: python main.py --mode upcoming --days 60 --ai --json",
        "matches": [],
        "count": 0,
        "source": "none",
        "updated": cst_now().isoformat(),
    }, 404


def load_worldcup_frontend_from_db():
    try:
        with session_scope() as session:
            rows = session.scalars(
                select(Match)
                .where(Match.competition_key == "worldcup")
                .order_by(Match.kickoff_time.asc(), Match.matchday.asc())
            ).all()
            if not rows:
                return None
            matches = []
            for row in rows:
                kickoff = row.kickoff_time
                if kickoff.tzinfo is None:
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                cst = kickoff.astimezone(timezone(timedelta(hours=8)))
                matches.append({
                    "id": row.source_id,
                    "league": "世界杯",
                    "competition": "FIFA World Cup 2026",
                    "stage": row.stage,
                    "group": row.group_name,
                    "date": cst.strftime("%Y-%m-%d"),
                    "time": cst.strftime("%H:%M"),
                    "matchday": row.matchday,
                    "home": row.home_team_code or row.home_team_name,
                    "homeName": row.home_team_name,
                    "homeFull": row.home_team_name,
                    "away": row.away_team_code or row.away_team_name,
                    "awayName": row.away_team_name,
                    "awayFull": row.away_team_name,
                    "status": match_status_from_kickoff(kickoff),
                    "minute": "",
                    "scoreH": row.score_home,
                    "scoreW": row.score_away,
                    "venue": row.venue,
                    "city": row.city,
                    "oddsAvailable": False,
                    "oddsMovement": {},
                    "pick": {
                        "type": "pending",
                        "label": "竞彩赔率待开售",
                        "conf": 0,
                        "reason": "FIFA 官方赛程已同步，开售后再生成正式投注建议",
                        "score": 0,
                        "tags": ["官方赛程"],
                    },
                })
            return {
                "count": len(matches),
                "matches": matches,
                "source": "database",
                "updated": cst_now().isoformat(),
                "mode": "database",
                "days": "",
            }
    except Exception as exc:
        print(f"[DB] load worldcup failed: {exc}")
        return None


def is_demo_recommendation_payload(payload):
    source_file = str(payload.get("source_file", ""))
    if source_file.endswith("data/jingcai_matches.csv"):
        return True
    for item in payload.get("recommendations", []):
        notes = " ".join(str(note) for note in item.get("notes", []))
        if "示例CSV" in notes:
            return True
    return False


def load_epl_frontend_payload():
    date_from = request.args.get("dateFrom") if has_request_context() else None
    date_to = request.args.get("dateTo") if has_request_context() else None
    date_from = date_from or "2025-08-01"
    date_to = date_to or "2026-05-31"

    if not API_KEY:
        return load_openfootball_epl_payload(date_from, date_to, "FOOTBALL_DATA_API_KEY not configured")

    url = f"{BASE}/competitions/PL/matches?dateFrom={date_from}&dateTo={date_to}"

    data = fetch_with_cache(f"pl_matches_{date_from}_{date_to}", url, ttl=120)
    if not data:
        return load_openfootball_epl_payload(date_from, date_to, "Failed to fetch football-data.org Premier League data")

    matches = []
    for m in data.get("matches", []):
        home_t = m.get("homeTeam", {})
        away_t = m.get("awayTeam", {})
        status = map_status(m.get("status", ""))
        score = m.get("score", {})
        ft = score.get("fullTime") or {}
        ht = score.get("halfTime") or {}
        match_time = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        cst = match_time.astimezone(timezone(timedelta(hours=8)))

        minute = ""
        if status in ("live", "ht"):
            elapsed = cst_now() - cst
            minute = f"{int(elapsed.total_seconds() // 60)}'"

        matches.append({
            "id": m["id"],
            "league": "英超",
            "competition": "Premier League",
            "date": cst.strftime("%Y-%m-%d"),
            "time": cst.strftime("%H:%M"),
            "matchday": m.get("matchday"),
            "home": home_t.get("tla", ""),
            "homeName": "",
            "homeFull": home_t.get("name", ""),
            "away": away_t.get("tla", ""),
            "awayName": "",
            "awayFull": away_t.get("name", ""),
            "status": status,
            "minute": minute,
            "scoreH": ft.get("home") if ft.get("home") is not None else ht.get("home"),
            "scoreW": ft.get("away") if ft.get("away") is not None else ht.get("away"),
            "oddsAvailable": False,
            "pick": {
                "type": "pending",
                "label": "AI分析待接入",
                "conf": 0.0,
                "reason": "英超 API 数据已接入，竞彩分析暂未接入英超玩法",
            },
        })

    enrich_fixture_details(matches, "epl")
    attach_odds(matches, "epl")
    upsert_matches_from_frontend(matches, "epl", "2025", "football-data.org")
    return {
        "count": len(matches),
        "matches": matches,
        "source": "football-data.org",
        "updated": cst_now().isoformat(),
    }, 200


def load_openfootball_epl_payload(date_from, date_to, fallback_reason=""):
    data = fetch_with_cache("openfootball_epl_2025_26", OPENFOOTBALL_EPL_2025_26, ttl=3600)
    if not data:
        return {
            "error": fallback_reason or "Failed to fetch OpenFootball Premier League data",
            "matches": [],
            "count": 0,
            "source": "openfootball",
            "updated": cst_now().isoformat(),
        }, 503

    matches = []
    for idx, m in enumerate(data.get("matches", []), start=1):
        date = m.get("date", "")
        if date_from and date < date_from:
            continue
        if date_to and date > date_to:
            continue

        score = m.get("score") or {}
        ft = score.get("ft") or score.get("fullTime") or []
        score_h = ft[0] if isinstance(ft, list) and len(ft) >= 2 else score.get("team1")
        score_w = ft[1] if isinstance(ft, list) and len(ft) >= 2 else score.get("team2")
        status = "finished" if score_h is not None and score_w is not None else "upcoming"
        round_text = m.get("round", "")
        matchday = ""
        if round_text.startswith("Matchday "):
            matchday = round_text.replace("Matchday ", "")

        matches.append({
            "id": f"openfootball-epl-2025-{idx}",
            "league": "英超",
            "competition": "Premier League",
            "date": date,
            "time": m.get("time", ""),
            "matchday": matchday,
            "home": team_tla(m.get("team1", "")),
            "homeName": "",
            "homeFull": m.get("team1", ""),
            "away": team_tla(m.get("team2", "")),
            "awayName": "",
            "awayFull": m.get("team2", ""),
            "status": status,
            "minute": "",
            "scoreH": score_h,
            "scoreW": score_w,
            "oddsAvailable": False,
            "pick": {
                "type": "pending",
                "label": "AI分析待接入",
                "conf": 0.0,
                "reason": "英超公开赛程已接入，等待指数和球队模型补齐后生成建议",
            },
        })

    enrich_fixture_details(matches, "epl")
    attach_odds(matches, "epl")
    upsert_matches_from_frontend(matches, "epl", "2025", "openfootball")
    return {
        "count": len(matches),
        "matches": matches,
        "source": "openfootball",
        "fallback_reason": fallback_reason,
        "updated": cst_now().isoformat(),
    }, 200


def team_tla(name):
    mapping = {
        "Manchester City FC": "MCI",
        "Manchester United FC": "MUN",
        "Arsenal FC": "ARS",
        "Liverpool FC": "LIV",
        "Chelsea FC": "CHE",
        "Tottenham Hotspur FC": "TOT",
        "Newcastle United": "NEW",
        "Newcastle United FC": "NEW",
        "Aston Villa": "AVL",
        "Aston Villa FC": "AVL",
        "West Ham United": "WHU",
        "West Ham United FC": "WHU",
        "Brighton & Hove Albion": "BHA",
        "Brighton & Hove Albion FC": "BHA",
        "Crystal Palace FC": "CRY",
        "Everton FC": "EVE",
        "Nottingham Forest FC": "NFO",
        "Brentford FC": "BRE",
        "Fulham FC": "FUL",
        "Wolverhampton Wanderers FC": "WOL",
        "AFC Bournemouth": "BOU",
        "AFC Bournemouth FC": "BOU",
        "Leeds United FC": "LEE",
        "Burnley FC": "BUR",
        "Sunderland AFC": "SUN",
    }
    return mapping.get(name, "")


def canonical_team(value):
    text = str(value or "").lower()
    for token in [" football club", " fc", " afc", " cf", ".", "&"]:
        text = text.replace(token, " ")
    text = text.replace(" and ", " ")
    return " ".join(text.split())


def match_date_key(value):
    if not value:
        return ""
    return str(value)[:10]


def odds_team_key(home, away, date):
    teams = sorted([canonical_team(home), canonical_team(away)])
    return f"{match_date_key(date)}|{teams[0]}|{teams[1]}"


def league_odds_config(league_key):
    configs = {
        "epl": {
            "the_odds_api_sport": os.environ.get("THE_ODDS_API_EPL_SPORT", "soccer_epl"),
            "api_football_league": os.environ.get("API_FOOTBALL_EPL_LEAGUE", "39"),
            "api_football_season": os.environ.get("API_FOOTBALL_EPL_SEASON", "2025"),
        },
        "worldcup": {
            "the_odds_api_sport": os.environ.get("THE_ODDS_API_WORLDCUP_SPORT", "soccer_fifa_world_cup"),
            "api_football_league": os.environ.get("API_FOOTBALL_WORLDCUP_LEAGUE", "1"),
            "api_football_season": os.environ.get("API_FOOTBALL_WORLDCUP_SEASON", "2026"),
        },
    }
    return configs.get(league_key, {})


def decimal_or_none(value):
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_the_odds_api_odds(league_key):
    cfg = league_odds_config(league_key)
    sport = cfg.get("the_odds_api_sport")
    if not ODDS_API_KEY or not sport:
        return {}
    query = urlencode({
        "apiKey": ODDS_API_KEY,
        "regions": ODDS_API_REGIONS,
        "markets": "h2h,totals",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    })
    url = f"{THE_ODDS_API_BASE}/sports/{sport}/odds/?{query}"
    data = fetch_public_with_cache(f"the_odds_{league_key}_{ODDS_API_REGIONS}", url, ttl=300)
    if not isinstance(data, list):
        return {}

    odds_by_match = {}
    for item in data:
        home = item.get("home_team", "")
        away = item.get("away_team", "")
        date = match_date_key(item.get("commence_time", ""))
        key = odds_team_key(home, away, date)
        bookmaker = (item.get("bookmakers") or [{}])[0]
        markets = bookmaker.get("markets") or []
        match_odds = {
            "source": "the-odds-api",
            "bookmaker": bookmaker.get("title", ""),
            "updated": bookmaker.get("last_update", ""),
            "h2h": {},
            "totals": [],
        }
        for market in markets:
            if market.get("key") == "h2h":
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name", "")
                    price = decimal_or_none(outcome.get("price"))
                    if canonical_team(name) == canonical_team(home):
                        match_odds["h2h"]["home"] = price
                    elif canonical_team(name) == canonical_team(away):
                        match_odds["h2h"]["away"] = price
                    elif name.lower() == "draw":
                        match_odds["h2h"]["draw"] = price
            elif market.get("key") == "totals":
                for outcome in market.get("outcomes", []):
                    price = decimal_or_none(outcome.get("price"))
                    point = decimal_or_none(outcome.get("point"))
                    if price is not None:
                        match_odds["totals"].append({
                            "name": outcome.get("name", ""),
                            "point": point,
                            "price": price,
                        })
        odds_by_match[key] = match_odds
    return odds_by_match


def fetch_api_football_odds(league_key):
    cfg = league_odds_config(league_key)
    league = cfg.get("api_football_league")
    season = cfg.get("api_football_season")
    if not API_FOOTBALL_KEY or not league or not season:
        return {}
    url = f"{API_FOOTBALL_BASE}/odds?league={league}&season={season}"
    data = fetch_api_football_with_cache(f"api_football_odds_{league}_{season}", url, ttl=600)
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
        match_odds = {
            "source": "api-football",
            "bookmaker": bookmaker.get("name", ""),
            "updated": item.get("update", ""),
            "h2h": {},
            "totals": [],
        }
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
                        match_odds["totals"].append({
                            "name": parts[0] if parts else raw,
                            "point": decimal_or_none(parts[-1] if parts else None),
                            "price": price,
                        })
        odds_by_match[odds_team_key(home, away, date)] = match_odds
    return odds_by_match


def fetch_league_odds(league_key):
    odds = fetch_the_odds_api_odds(league_key)
    if odds:
        return odds
    odds = fetch_api_football_odds(league_key)
    if odds:
        return odds
    return fetch_sporttery_odds(league_key)


def enrich_fixture_details(matches, league_key):
    fixtures = fetch_api_football_fixtures(league_key)
    sportsdb_events = fetch_thesportsdb_events(league_key) if not fixtures else {}
    if not fixtures and not sportsdb_events:
        return matches
    for match in matches:
        key = odds_team_key(
            match.get("homeFull") or match.get("homeName") or match.get("home"),
            match.get("awayFull") or match.get("awayName") or match.get("away"),
            match.get("date", ""),
        )
        item = fixtures.get(key)
        if item:
            fixture = item.get("fixture") or {}
            venue = fixture.get("venue") or {}
            status = fixture.get("status") or {}
            goals = item.get("goals") or {}
            teams = item.get("teams") or {}
            home_team = teams.get("home") or {}
            away_team = teams.get("away") or {}
            dt = fixture.get("date")
            if dt:
                try:
                    cst = datetime.fromisoformat(dt.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=8)))
                    match["date"] = cst.strftime("%Y-%m-%d")
                    match["time"] = cst.strftime("%H:%M")
                except Exception:
                    pass
            if home_team.get("name"):
                match["homeFull"] = home_team.get("name")
            if away_team.get("name"):
                match["awayFull"] = away_team.get("name")
            if venue.get("name"):
                match["venue"] = venue.get("name")
            if venue.get("city"):
                match["city"] = venue.get("city")
            mapped_status = map_api_football_status(status.get("short"))
            if mapped_status:
                match["status"] = mapped_status
            if goals.get("home") is not None:
                match["scoreH"] = goals.get("home")
            if goals.get("away") is not None:
                match["scoreW"] = goals.get("away")
            match["fixtureSource"] = "api-football"
            match["fixtureId"] = fixture.get("id")
            match["fixtureSeason"] = league_odds_config(league_key).get("api_football_season", "")
            raw = dict(match.get("raw") or {})
            raw.update({
                "fixtureId": fixture.get("id"),
                "fixtureSource": "api-football",
                "fixtureSeason": league_odds_config(league_key).get("api_football_season", ""),
                "apiFootballFixture": item,
            })
            match["raw"] = raw
            continue

        event = sportsdb_events.get(key)
        if not event:
            continue
        if event.get("strHomeTeam"):
            match["homeFull"] = event.get("strHomeTeam")
        if event.get("strAwayTeam"):
            match["awayFull"] = event.get("strAwayTeam")
        if event.get("strVenue"):
            match["venue"] = event.get("strVenue")
        if event.get("intHomeScore") not in {None, ""}:
            match["scoreH"] = int(event.get("intHomeScore"))
        if event.get("intAwayScore") not in {None, ""}:
            match["scoreW"] = int(event.get("intAwayScore"))
        if match.get("scoreH") is not None and match.get("scoreW") is not None:
            match["status"] = "finished"
        match["sportsdbEventId"] = event.get("idEvent")
        raw = dict(match.get("raw") or {})
        raw.update({
            "sportsdbEventId": event.get("idEvent"),
            "fixtureSource": "thesportsdb",
            "sportsdbSeason": thesportsdb_config(league_key).get("season", ""),
            "theSportsDBEvent": event,
        })
        match["raw"] = raw
    return matches


def attach_odds(matches, league_key):
    odds_by_match = fetch_league_odds(league_key)
    if not odds_by_match:
        return matches
    for match in matches:
        key = odds_team_key(
            match.get("homeFull") or match.get("homeName") or match.get("home"),
            match.get("awayFull") or match.get("awayName") or match.get("away"),
            match.get("date", ""),
        )
        odds = odds_by_match.get(key)
        if not odds:
            continue
        match["oddsAvailable"] = True
        match["odds"] = odds
        h2h = odds.get("h2h") or {}
        label_parts = []
        if h2h.get("home"):
            label_parts.append(f"H {h2h['home']:.2f}")
        if h2h.get("draw"):
            label_parts.append(f"D {h2h['draw']:.2f}")
        if h2h.get("away"):
            label_parts.append(f"A {h2h['away']:.2f}")
        match["pick"] = {
            "type": "odds",
            "label": " / ".join(label_parts) if label_parts else "指数已接入",
            "conf": 0.0,
            "reason": "指数已接入，等待 AI 复评",
        }
    persist_odds_snapshots(matches, league_key)
    return matches


def load_combined_matches_payload():
    worldcup_payload, worldcup_status = load_worldcup_frontend_payload()
    epl_payload, epl_status = load_epl_frontend_payload()
    matches = []
    if epl_status == 200:
        matches.extend(epl_payload.get("matches", []))
    if worldcup_status == 200:
        matches.extend(worldcup_payload.get("matches", []))

    status = 200 if matches else max(worldcup_status, epl_status)
    return {
        "count": len(matches),
        "matches": matches,
        "sources": {
            "epl": {
                "status": epl_status,
                "count": epl_payload.get("count", 0),
                "source": epl_payload.get("source", "football-data.org"),
                "error": epl_payload.get("error", ""),
            },
            "worldcup": {
                "status": worldcup_status,
                "count": worldcup_payload.get("count", 0),
                "source": worldcup_payload.get("source", ""),
                "error": worldcup_payload.get("error", ""),
            },
        },
        "updated": cst_now().isoformat(),
    }, status


def sync_run_payload(row):
    if not row:
        return None
    return {
        "id": row.id,
        "jobName": row.job_name,
        "trigger": row.trigger,
        "status": row.status,
        "startedAt": row.started_at.isoformat() if row.started_at else "",
        "finishedAt": row.finished_at.isoformat() if row.finished_at else "",
        "durationMs": row.duration_ms,
        "summary": row.summary or {},
        "error": row.error or "",
    }


def latest_sync_run():
    try:
        with session_scope() as session:
            row = session.scalars(
                select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1)
            ).first()
            return sync_run_payload(row)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def current_sync_profile():
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        live_count = len(session.scalars(
            select(Match.id).where(
                Match.kickoff_time >= now - timedelta(hours=SYNC_LIVE_LOOKBACK_HOURS),
                Match.kickoff_time <= now + timedelta(hours=2),
            )
        ).all())
        if live_count:
            return {"name": "live", "reason": f"{live_count} matches are live or near live"}

        prematch_count = len(session.scalars(
            select(Match.id).where(
                Match.kickoff_time > now,
                Match.kickoff_time <= now + timedelta(hours=SYNC_PREMATCH_HOURS),
            )
        ).all())
        if prematch_count:
            return {"name": "prematch", "reason": f"{prematch_count} matches kick off within {SYNC_PREMATCH_HOURS}h"}

        postmatch_count = len(session.scalars(
            select(Match.id).where(
                Match.kickoff_time <= now,
                Match.kickoff_time >= now - timedelta(hours=SYNC_RECENT_FINISHED_HOURS),
            )
        ).all())
        if postmatch_count:
            return {"name": "postmatch", "reason": f"{postmatch_count} matches finished within {SYNC_RECENT_FINISHED_HOURS}h"}
    return {"name": "base", "reason": "no near kickoff or recent match window"}


def sync_interval_seconds(profile_name):
    minutes = {
        "live": SYNC_LIVE_INTERVAL_MINUTES,
        "prematch": SYNC_PREMATCH_INTERVAL_MINUTES,
        "postmatch": SYNC_POSTMATCH_INTERVAL_MINUTES,
        "base": SYNC_BASE_INTERVAL_MINUTES,
    }.get(profile_name, SYNC_BASE_INTERVAL_MINUTES)
    return minutes * 60


def run_layered_data_sync(profile_name):
    sporttery_sync = sync_sporttery_match_data()
    football_data_uk_sync = sync_football_data_uk_match_data()
    if profile_name == "base":
        return {
            "profile": profile_name,
            "sportteryMatchData": sporttery_sync,
            "footballDataUk": football_data_uk_sync,
            "matchData": {"enabled": True, "synced": 0, "reason": "base profile skips rich match-data calls"},
            "sportsdbMatchData": {"enabled": True, "synced": 0, "reason": "base profile skips rich match-data calls"},
        }

    if profile_name == "live":
        limit = SYNC_LIVE_MATCH_DATA_LIMIT
    elif profile_name == "postmatch":
        limit = SYNC_POSTMATCH_MATCH_DATA_LIMIT
    else:
        limit = SYNC_PREMATCH_MATCH_DATA_LIMIT

    match_data_sync = sync_api_football_match_data(limit=limit)
    sportsdb_sync = sync_thesportsdb_match_data(limit=limit) if not match_data_sync.get("synced") else {
        "enabled": True,
        "synced": 0,
        "reason": "api-football already synced match data",
    }
    return {
        "profile": profile_name,
        "sportteryMatchData": sporttery_sync,
        "footballDataUk": football_data_uk_sync,
        "matchData": match_data_sync,
        "sportsdbMatchData": sportsdb_sync,
    }


def execute_sync(trigger="manual"):
    if not SYNC_LOCK.acquire(blocking=False):
        summary = {"message": "sync already running"}
        with session_scope() as session:
            row = SyncRun(
                job_name="data_sync",
                trigger=trigger,
                status="skipped",
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                duration_ms=0,
                summary=summary,
            )
            session.add(row)
            session.flush()
            run_payload = sync_run_payload(row)
        return {"ok": False, "skipped": True, "syncRun": run_payload, **summary}

    started = datetime.now(timezone.utc)
    run_id = None
    try:
        with session_scope() as session:
            row = SyncRun(job_name="data_sync", trigger=trigger, status="running", started_at=started)
            session.add(row)
            session.flush()
            run_id = row.id

        epl_payload, epl_status = load_epl_frontend_payload()
        worldcup_payload, worldcup_status = load_worldcup_frontend_payload()
        analysis_files = persist_analysis_files()
        sync_profile = current_sync_profile()
        sync_profile["aiPrefillBeforeQueue"] = AI_PREFILL_CONTEXT_BEFORE_QUEUE
        layered_sync = run_layered_data_sync(sync_profile["name"])
        match_data_sync = layered_sync["matchData"]
        sportsdb_sync = layered_sync["sportsdbMatchData"]
        sporttery_sync = layered_sync["sportteryMatchData"]
        football_data_uk_sync = layered_sync["footballDataUk"]
        ai_queue = enqueue_analysis_jobs()
        ai_run = run_analysis_jobs()
        counts = db_counts()
        summary = {
            "epl": {
                "status": epl_status,
                "count": epl_payload.get("count", 0),
                "source": epl_payload.get("source", ""),
                "error": epl_payload.get("error", ""),
            },
            "worldcup": {
                "status": worldcup_status,
                "count": worldcup_payload.get("count", 0),
                "source": worldcup_payload.get("source", ""),
                "error": worldcup_payload.get("error", ""),
            },
            "analysisFiles": analysis_files,
            "syncProfile": sync_profile,
            "nextIntervalSeconds": sync_interval_seconds(sync_profile["name"]),
            "matchData": match_data_sync,
            "sportsdbMatchData": sportsdb_sync,
            "sportteryMatchData": sporttery_sync,
            "footballDataUk": football_data_uk_sync,
            "aiQueue": ai_queue,
            "aiRun": ai_run,
            "database": counts,
        }
        ok = epl_status == 200 or worldcup_status == 200
        status = "success" if ok else "failed"
        finished = datetime.now(timezone.utc)

        with session_scope() as session:
            row = session.get(SyncRun, run_id)
            row.status = status
            row.finished_at = finished
            row.duration_ms = int((finished - started).total_seconds() * 1000)
            row.summary = summary
            row.error = "" if ok else "no match source returned successful data"
            session.flush()
            run_payload = sync_run_payload(row)

        return {
            "ok": ok,
            "epl": summary["epl"],
            "worldcup": summary["worldcup"],
            "analysis_files": analysis_files,
            "sync_profile": sync_profile,
            "next_interval_seconds": sync_interval_seconds(sync_profile["name"]),
            "match_data": match_data_sync,
            "sportsdb_match_data": sportsdb_sync,
            "sporttery_match_data": sporttery_sync,
            "football_data_uk": football_data_uk_sync,
            "ai_queue": ai_queue,
            "ai_run": ai_run,
            "database": counts,
            "syncRun": run_payload,
            "updated": cst_now().isoformat(),
        }
    except Exception as exc:
        finished = datetime.now(timezone.utc)
        if run_id:
            try:
                with session_scope() as session:
                    row = session.get(SyncRun, run_id)
                    row.status = "failed"
                    row.finished_at = finished
                    row.duration_ms = int((finished - started).total_seconds() * 1000)
                    row.error = str(exc)
                    row.summary = {"database": db_counts()}
            except Exception as log_exc:
                print(f"[Sync] failed to update sync_runs: {log_exc}")
        print(f"[Sync] {trigger} sync failed: {exc}")
        return {"ok": False, "error": str(exc), "updated": cst_now().isoformat()}
    finally:
        SYNC_LOCK.release()


def scheduler_loop():
    if SYNC_STARTUP_DELAY_SECONDS:
        time.sleep(SYNC_STARTUP_DELAY_SECONDS)
    while True:
        result = execute_sync(trigger="scheduled")
        interval = int(result.get("next_interval_seconds") or AUTO_SYNC_INTERVAL_MINUTES * 60)
        time.sleep(max(60, interval))


def start_scheduler_once():
    global SCHEDULER_STARTED
    if SCHEDULER_STARTED or not AUTO_SYNC_ENABLED:
        return
    SCHEDULER_STARTED = True
    thread = threading.Thread(target=scheduler_loop, name="data-sync-scheduler", daemon=True)
    thread.start()
    print(f"[Sync] scheduler enabled: base every {SYNC_BASE_INTERVAL_MINUTES} minutes")


@app.route("/api/health")
def health():
    worldcup_payload, worldcup_status = load_worldcup_frontend_payload()
    epl_payload, epl_status = load_epl_frontend_payload()
    database_status = {**DB_STATUS}
    if database_status.get("ok"):
        try:
            database_status["counts"] = db_counts()
        except Exception as exc:
            database_status["ok"] = False
            database_status["error"] = str(exc)
    return jsonify({
        "ok": (worldcup_status == 200 or epl_status == 200) and database_status.get("ok", False),
        "database": database_status,
        "football_data_api_key": bool(API_KEY),
        "epl_matches": epl_payload.get("count", 0),
        "epl_status": epl_status,
        "worldcup_matches": worldcup_payload.get("count", 0),
        "worldcup_status": worldcup_status,
        "source": "combined",
        "sync": {
            "enabled": AUTO_SYNC_ENABLED,
            "intervalMinutes": AUTO_SYNC_INTERVAL_MINUTES,
            "profile": current_sync_profile(),
            "profileIntervalsMinutes": {
                "base": SYNC_BASE_INTERVAL_MINUTES,
                "prematch": SYNC_PREMATCH_INTERVAL_MINUTES,
                "live": SYNC_LIVE_INTERVAL_MINUTES,
                "postmatch": SYNC_POSTMATCH_INTERVAL_MINUTES,
            },
            "latest": latest_sync_run(),
        },
        "ai": {
            "jobs": analysis_job_stats(),
        },
        "updated": cst_now().isoformat(),
    }), 200


@app.route("/api/admin/sync", methods=["POST"])
def admin_sync():
    result = execute_sync(trigger="manual")
    return jsonify(result), 200 if result.get("ok") or result.get("skipped") else 500


@app.route("/api/admin/sync-runs")
def admin_sync_runs():
    limit = min(100, max(1, int(request.args.get("limit", "20"))))
    with session_scope() as session:
        rows = session.scalars(
            select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)
        ).all()
        return jsonify({
            "count": len(rows),
            "runs": [sync_run_payload(row) for row in rows],
            "updated": cst_now().isoformat(),
        })


def analysis_job_payload(row):
    return {
        "id": row.id,
        "matchId": row.match_id,
        "status": row.status,
        "priority": row.priority,
        "model": row.model,
        "promptHash": row.prompt_hash,
        "attempts": row.attempts,
        "totalTokens": row.total_tokens,
        "error": row.error,
        "createdAt": row.created_at.isoformat() if row.created_at else "",
        "updatedAt": row.updated_at.isoformat() if row.updated_at else "",
    }


@app.route("/api/admin/analysis/enqueue", methods=["POST"])
def admin_analysis_enqueue():
    if not is_admin_request():
        return jsonify({"error": "Forbidden"}), 403
    result = enqueue_analysis_jobs()
    return jsonify({"ok": True, **result, "stats": analysis_job_stats(), "updated": cst_now().isoformat()})


@app.route("/api/admin/analysis/run", methods=["POST"])
def admin_analysis_run():
    if not is_admin_request():
        return jsonify({"error": "Forbidden"}), 403
    result = run_analysis_jobs()
    return jsonify({"ok": bool(result.get("enabled")), **result, "stats": analysis_job_stats(), "updated": cst_now().isoformat()})


@app.route("/api/admin/analysis/jobs")
def admin_analysis_jobs():
    if not is_admin_request():
        return jsonify({"error": "Forbidden"}), 403
    limit = min(100, max(1, int(request.args.get("limit", "30"))))
    with session_scope() as session:
        rows = session.scalars(
            select(AnalysisJob).order_by(AnalysisJob.created_at.desc()).limit(limit)
        ).all()
        return jsonify({
            "count": len(rows),
            "jobs": [analysis_job_payload(row) for row in rows],
            "stats": analysis_job_stats(),
            "updated": cst_now().isoformat(),
        })


@app.route("/api/admin/match-data/sync", methods=["POST"])
def admin_match_data_sync():
    if not is_admin_request():
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    limit = data.get("limit")
    api_result = sync_api_football_match_data(limit=limit) if limit is not None else sync_api_football_match_data()
    sportsdb_result = sync_thesportsdb_match_data(limit=limit) if limit is not None else sync_thesportsdb_match_data()
    sporttery_result = sync_sporttery_match_data(limit=limit) if limit is not None else sync_sporttery_match_data()
    football_data_uk_result = sync_football_data_uk_match_data(limit=limit) if limit is not None else sync_football_data_uk_match_data()
    return jsonify({
        "ok": True,
        "apiFootball": api_result,
        "theSportsDB": sportsdb_result,
        "sporttery": sporttery_result,
        "footballDataUk": football_data_uk_result,
        "database": db_counts(),
        "updated": cst_now().isoformat(),
    })


@app.route("/api/system/status")
def system_status():
    schedule = file_info("data/worldcup_2026_schedule.json")
    teams = file_info("data/teams.json")
    recommendations = load_json("data/recommendations.json", {})
    recommendation_count = len(recommendations.get("recommendations", [])) if isinstance(recommendations, dict) else 0
    candidate_count = 0
    ai_ready_count = 0
    parlay_count = len(recommendations.get("parlay", [])) if isinstance(recommendations, dict) else 0
    for item in recommendations.get("recommendations", []) if isinstance(recommendations, dict) else []:
        pack = item.get("openclaw_analysis") or {}
        if pack.get("status") == "ready_for_openclaw_analysis":
            ai_ready_count += 1
        candidate_count += len(pack.get("candidate_bets") or [])

    odds_movements = load_json("data/odds_movements.json", {})
    archive_index = load_json("data/final_reports/index.json", [])
    reports_dir = ROOT / "data/reports"
    report_count = len(list(reports_dir.glob("*.md"))) if reports_dir.exists() else 0
    settlement = file_info("data/settlement_report.json")

    modules = [
        {"name": "赛程数据", "status": "完成" if schedule["count"] >= 104 else "待同步", "summary": f"FIFA 官方赛程 {schedule['count']} 场", "href": "report-preview.html"},
        {"name": "球队画像", "status": "完成" if teams["count"] >= 48 else "待补齐", "summary": f"{teams['count']} 支球队画像", "href": "ai-analysis-pack.html"},
        {"name": "候选生成", "status": "完成" if candidate_count else "待指数", "summary": f"{recommendation_count} 场分析，{candidate_count} 个候选", "href": "ai-analysis-pack.html"},
        {"name": "AI 分析包", "status": "完成" if ai_ready_count else "待生成", "summary": f"{ai_ready_count} 场 ready_for_openclaw_analysis", "href": "ai-analysis-pack.html"},
        {"name": "指数追踪", "status": "完成" if odds_movements else "待快照", "summary": f"{len(odds_movements.get('changes', [])) if isinstance(odds_movements, dict) else 0} 条显著变化", "href": "skill-and-log.html"},
        {"name": "报告输出", "status": "完成" if report_count else "待生成", "summary": f"Markdown 报告 {report_count} 份，JSON 持续输出", "href": "report-preview.html"},
        {"name": "实时推送", "status": "已配置", "summary": "Telegram 推送开关已接入", "href": "skill-and-log.html"},
        {"name": "赛后复盘", "status": "可用" if settlement["exists"] else "可运行", "summary": "赛果录入后自动结算准确率", "href": "settlement-report.html"},
        {"name": "归档系统", "status": "完成" if archive_index else "可用", "summary": f"归档记录 {len(archive_index) if isinstance(archive_index, list) else 0} 条", "href": "report-preview.html"},
        {"name": "混合过关", "status": "完成" if parlay_count else "待候选", "summary": f"{parlay_count} 个组合参考", "href": "ai-analysis-pack.html"},
    ]
    return jsonify({
        "updated": cst_now().isoformat(),
        "modules": modules,
        "summary": {
            "schedule_matches": schedule["count"],
            "team_profiles": teams["count"],
            "recommendation_matches": recommendation_count,
            "candidate_bets": candidate_count,
            "ai_ready": ai_ready_count,
            "reports": report_count,
            "parlay": parlay_count,
        },
    })


@app.route("/api/epl/matches")
def epl_matches():
    """Get Premier League fixtures/results from football-data.org."""
    payload, status = load_epl_frontend_payload()
    return jsonify(payload), status


@app.route("/api/worldcup/matches")
def worldcup_matches():
    """Get latest World Cup analysis matches from data/recommendations.json"""
    payload, status = load_worldcup_frontend_payload()
    return jsonify(payload), status


@app.route("/api/matches")
def matches_alias():
    """Combined match feed for the NAS frontend."""
    payload, status = load_combined_matches_payload()
    return jsonify(payload), status


def match_matches_league(item, league_key):
    text = f"{item.get('league', '')} {item.get('competition', '')}".lower()
    if league_key == "worldcup":
        return "world cup" in text or "世界杯" in text
    if league_key == "epl":
        return "premier league" in text or "英超" in text
    return False


def odds_snapshot_payload(row):
    return {
        "id": row.id,
        "market": row.market,
        "selection": row.selection,
        "price": row.price,
        "point": row.point,
        "source": row.source,
        "bookmaker": row.bookmaker,
        "capturedAt": row.captured_at.isoformat() if row.captured_at else "",
    }


def current_odds_sources(snapshot_rows):
    sources = {}
    for row in snapshot_rows:
        key = f"{row.source}|{row.bookmaker or row.source}"
        item = sources.setdefault(key, {
            "source": row.source,
            "bookmaker": row.bookmaker or row.source,
            "updated": row.captured_at.isoformat() if row.captured_at else "",
            "h2h": {},
            "totals": [],
            "spreads": [],
        })
        if row.captured_at and row.captured_at.isoformat() > item["updated"]:
            item["updated"] = row.captured_at.isoformat()
        if row.market == "h2h":
            prev = item["h2h"].get(row.selection)
            if not prev or row.captured_at.isoformat() >= prev.get("capturedAt", ""):
                item["h2h"][row.selection] = {
                    "price": row.price,
                    "capturedAt": row.captured_at.isoformat() if row.captured_at else "",
                }
        elif row.market == "totals":
            total_key = f"{row.selection}|{row.point}"
            existing = None
            for total in item["totals"]:
                if total.get("_key") == total_key:
                    existing = total
                    break
            payload = {
                "_key": total_key,
                "selection": row.selection,
                "point": row.point,
                "price": row.price,
                "capturedAt": row.captured_at.isoformat() if row.captured_at else "",
            }
            if existing:
                if payload["capturedAt"] >= existing.get("capturedAt", ""):
                    existing.update(payload)
            else:
                item["totals"].append(payload)
        elif row.market == "spread":
            spread_key = f"{row.selection}|{row.point}"
            existing = None
            for spread in item["spreads"]:
                if spread.get("_key") == spread_key:
                    existing = spread
                    break
            payload = {
                "_key": spread_key,
                "selection": row.selection,
                "point": row.point,
                "price": row.price,
                "capturedAt": row.captured_at.isoformat() if row.captured_at else "",
            }
            if existing:
                if payload["capturedAt"] >= existing.get("capturedAt", ""):
                    existing.update(payload)
            else:
                item["spreads"].append(payload)

    result = []
    for item in sources.values():
        h2h = {
            selection: payload["price"]
            for selection, payload in item["h2h"].items()
        }
        totals = [
            {key: value for key, value in total.items() if key != "_key"}
            for total in item["totals"]
        ]
        spreads = [
            {key: value for key, value in spread.items() if key != "_key"}
            for spread in item["spreads"]
        ]
        result.append({
            "source": item["source"],
            "bookmaker": item["bookmaker"],
            "updated": item["updated"],
            "h2h": h2h,
            "totals": totals,
            "spreads": spreads,
        })
    return sorted(result, key=lambda item: item.get("updated", ""), reverse=True)


def recent_snapshot_batch(snapshot_rows):
    if not snapshot_rows:
        return []
    latest = max((row.captured_at for row in snapshot_rows if row.captured_at), default=None)
    if not latest:
        return []
    latest_minute = latest.replace(second=0, microsecond=0)
    deduped = {}
    for row in snapshot_rows:
        if not row.captured_at or row.captured_at.replace(second=0, microsecond=0) != latest_minute:
            continue
        key = (row.source, row.bookmaker, row.market, row.selection, row.point)
        existing = deduped.get(key)
        if not existing or row.captured_at >= existing.captured_at:
            deduped[key] = row
    rows = sorted(
        deduped.values(),
        key=lambda row: (row.market, row.selection, row.source, row.bookmaker),
    )
    return [odds_snapshot_payload(row) for row in rows][:30]


def analysis_payload(row, plan="free"):
    payload = row.payload or {}
    analysis = payload.get("analysis") if isinstance(payload, dict) else None
    if plan != "pro" and isinstance(analysis, dict):
        payload = {
            **payload,
            "analysis": {
                "summary": analysis.get("summary", ""),
                "confidence": analysis.get("confidence", 0),
                "best_pick": analysis.get("best_pick", {}),
                "pro_locked": True,
            },
        }
    return {
        "id": row.id,
        "status": row.status,
        "model": row.model,
        "payload": payload,
        "markdown": (row.markdown or "") if plan == "pro" else "",
        "createdAt": row.created_at.isoformat() if row.created_at else "",
    }


def match_data_payload(rows):
    result = {}
    for row in rows:
        result[row.data_type] = {
            "source": row.source,
            "payload": row.payload or {},
            "fetchedAt": row.fetched_at.isoformat() if row.fetched_at else "",
        }
    return result


@app.route("/api/matches/<league_key>/<path:source_id>")
def match_detail(league_key, source_id):
    """Single match detail for the frontend detail panel."""
    if league_key not in {"worldcup", "epl"}:
        return jsonify({"error": "Unsupported league"}), 404
    plan = current_plan()

    payload, status = load_combined_matches_payload()
    if status != 200:
        return jsonify({"error": "Match feed unavailable"}), status

    match = None
    for item in payload.get("matches", []):
        if str(item.get("id", "")) == source_id and match_matches_league(item, league_key):
            match = item
            break
    if not match:
        return jsonify({"error": "Match not found"}), 404

    snapshots = []
    current_sources = []
    recent_batch = []
    analyses = []
    rich_data = {}
    db_match_id = None
    try:
        with session_scope() as session:
            row = session.scalar(
                select(Match).where(Match.competition_key == league_key, Match.source_id == source_id)
            )
            if row:
                db_match_id = row.id
                snapshot_rows = session.scalars(
                    select(OddsSnapshot)
                    .where(OddsSnapshot.match_id == row.id)
                    .order_by(OddsSnapshot.captured_at.desc())
                    .limit(120)
                ).all()
                snapshots = [
                    odds_snapshot_payload(item)
                    for item in snapshot_rows
                ]
                current_sources = current_odds_sources(snapshot_rows)
                recent_batch = recent_snapshot_batch(snapshot_rows)
                analyses = [
                    analysis_payload(item, plan)
                    for item in session.scalars(
                        select(AnalysisResult)
                        .where(AnalysisResult.match_id == row.id)
                        .order_by(AnalysisResult.created_at.desc())
                        .limit(10)
                    ).all()
                ]
                rich_rows = session.scalars(
                    select(MatchData)
                    .where(MatchData.match_id == row.id)
                    .order_by(MatchData.fetched_at.desc())
                ).all()
                rich_data = match_data_payload(rich_rows)
    except Exception as exc:
        print(f"[DB] match detail failed: {exc}")

    return jsonify({
        "match": match,
        "league": league_key,
        "dbMatchId": db_match_id,
        "subscription": {"plan": plan},
        "oddsSources": current_sources,
        "recentOddsBatch": recent_batch,
        "oddsSnapshots": snapshots,
        "matchData": rich_data,
        "analysisResults": analyses,
        "updated": cst_now().isoformat(),
    })


@app.route("/api/worldcup/recommendations")
def worldcup_recommendations():
    """Get raw World Cup recommendation payload"""
    payload = load_json("data/recommendations.json", {})
    if not payload:
        return jsonify({"error": "data/recommendations.json not found. Run main.py --ai --json first."}), 404
    return jsonify(payload)


@app.route("/api/epl/standings")
def epl_standings():
    """Get Premier League standings"""
    if not API_KEY:
        return jsonify({"error": "API key not configured"}), 503

    url = f"{BASE}/competitions/PL/standings?season=2025"
    data = fetch_with_cache("pl_standings_2025", url, ttl=3600)
    if not data:
        return jsonify({"error": "Failed to fetch"}), 500

    standings = []
    for s in data.get("standings", []):
        if s.get("type") == "TOTAL":
            for e in s.get("table", []):
                standings.append({
                    "pos": e["position"], "team": e["team"]["name"],
                    "short": e["team"]["tla"], "played": e["playedGames"],
                    "won": e["won"], "drawn": e["draw"], "lost": e["lost"],
                    "gf": e["goalsFor"], "ga": e["goalsAgainst"],
                    "gd": e["goalDifference"], "pts": e["points"],
                })

    return jsonify({"standings": standings})


@app.route("/api/epl/top-scorers")
def epl_scorers():
    """Get Premier League top scorers"""
    if not API_KEY:
        return jsonify({"error": "API key not configured"}), 503

    url = f"{BASE}/competitions/PL/scorers?limit=20"
    data = fetch_with_cache("pl_scorers_2025", url, ttl=3600)
    if not data:
        return jsonify({"error": "Failed to fetch"}), 500

    scorers = []
    for s in data.get("scorers", []):
        player = s.get("player", {})
        team = s.get("team", {})
        scorers.append({
            "name": player.get("name", ""),
            "team": team.get("name", ""),
            "goals": s.get("goals", 0),
            "assists": s.get("assists", 0),
        })

    return jsonify({"scorers": scorers})


if __name__ == "__main__":
    startup_payload, _ = load_combined_matches_payload()
    print(f"🚀 AI智球 server starting on :8088")
    print(f"🏟️ Matches loaded: {startup_payload.get('count', 0)}")
    print(f"📊 football-data.org API key: {'✅ configured' if API_KEY else '❌ not set'}")
    start_scheduler_once()
    app.run(host="0.0.0.0", port=8088, debug=False, threaded=True)
