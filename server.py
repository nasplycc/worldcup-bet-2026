"""
Flask backend for worldcup-bet-2026.

NAS deployment serves the frontend and World Cup analysis API from this process.
"""
from dotenv import load_dotenv
load_dotenv()
import os
import time
import threading
from urllib.parse import urlencode
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from flask import Flask, jsonify, has_request_context, request, send_from_directory
from sqlalchemy import select

from frontend_data import merge_worldcup_frontend, recommendations_to_frontend, schedule_to_frontend
from state import load_json
from db import AnalysisJob, AnalysisResult, Match, MatchData, OddsSnapshot, SyncRun, db_counts, init_db, seed_all, session_scope
from db_sync import persist_analysis_files, persist_fixture_metadata, persist_odds_snapshots, upsert_matches_from_frontend
from ai_pipeline import analysis_job_stats, enqueue_analysis_jobs, run_analysis_jobs
from auth_routes import auth_bp, current_plan, is_admin_request
from data_sources.api_football import fetch_fixtures as fetch_api_football_fixtures, fetch_odds as fetch_api_football_odds, status_from_short as map_api_football_status, sync_match_data as sync_api_football_match_data
from data_sources.football_data_uk import sync_match_data as sync_football_data_uk_match_data
from data_sources.sporttery import fetch_odds as fetch_sporttery_odds, sync_match_data as sync_sporttery_match_data
from data_sources.thesportsdb import config as thesportsdb_config, fetch_events as fetch_thesportsdb_events, sync_match_data as sync_thesportsdb_match_data

app = Flask(__name__, static_folder="docs", static_url_path="")
app.register_blueprint(auth_bp)
ROOT = Path(__file__).resolve().parent

# Config
API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
ODDS_API_KEY = os.environ.get("THE_ODDS_API_KEY", "") or os.environ.get("ODDS_API_KEY", "")
ODDS_API_REGIONS = os.environ.get("THE_ODDS_API_REGIONS", "eu")
BASE = "https://api.football-data.org/v4"
OPENFOOTBALL_EPL_2025_26 = "https://openfootball.github.io/england/2025-26/1-premierleague.json"
THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
CACHE = {}
CACHE_TTL = 60
AUTO_SYNC_ENABLED = os.environ.get("AUTO_SYNC_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
AUTO_SYNC_INTERVAL_MINUTES = max(5, int(os.environ.get("AUTO_SYNC_INTERVAL_MINUTES", "60")))
SYNC_STARTUP_DELAY_SECONDS = max(0, int(os.environ.get("SYNC_STARTUP_DELAY_SECONDS", "20")))
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
ODDS_HISTORY_QUERY_LIMIT = max(24, int(os.environ.get("ODDS_HISTORY_QUERY_LIMIT", "300")))
ODDS_HISTORY_DISPLAY_LIMIT = max(6, int(os.environ.get("ODDS_HISTORY_DISPLAY_LIMIT", "24")))
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


@app.route("/")
def index():
    return send_from_directory("docs", "index.html")


def map_status(status):
    m = {
        "SCHEDULED": "upcoming", "TIMED": "upcoming",
        "IN_PLAY": "live", "PAUSED": "ht",
        "FINISHED": "finished", "AET": "finished", "PEN": "finished",
        "POSTPONED": "postponed", "SUSPENDED": "postponed", "CANCELLED": "cancelled",
    }
    return m.get(status, "upcoming")


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
    persist_fixture_metadata(matches, "epl")
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
    persist_fixture_metadata(matches, "epl")
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
    aliases = {
        "ars": "arsenal", "arsenal fc": "arsenal", "阿森纳": "arsenal",
        "avl": "aston villa", "aston villa fc": "aston villa", "阿斯顿维拉": "aston villa",
        "bou": "bournemouth", "afc bournemouth": "bournemouth", "伯恩茅斯": "bournemouth",
        "bre": "brentford", "brentford fc": "brentford", "布伦特福德": "brentford",
        "bha": "brighton", "brighton & hove albion": "brighton", "brighton and hove albion": "brighton", "布莱顿": "brighton",
        "che": "chelsea", "chelsea fc": "chelsea", "切尔西": "chelsea",
        "cry": "crystal palace", "crystal palace fc": "crystal palace", "水晶宫": "crystal palace",
        "eve": "everton", "everton fc": "everton", "埃弗顿": "everton",
        "ful": "fulham", "fulham fc": "fulham", "富勒姆": "fulham",
        "lee": "leeds", "leeds united": "leeds", "利兹联": "leeds",
        "liv": "liverpool", "liverpool fc": "liverpool", "利物浦": "liverpool",
        "mci": "manchester city", "manchester city fc": "manchester city", "曼城": "manchester city",
        "mun": "manchester united", "manchester united fc": "manchester united", "曼联": "manchester united",
        "new": "newcastle", "newcastle united": "newcastle", "纽卡斯尔": "newcastle",
        "nfo": "nottingham forest", "nottingham forest fc": "nottingham forest", "诺丁汉森林": "nottingham forest",
        "sun": "sunderland", "sunderland afc": "sunderland", "桑德兰": "sunderland",
        "tot": "tottenham", "tottenham hotspur": "tottenham", "tottenham hotspur fc": "tottenham", "热刺": "tottenham",
        "whu": "west ham", "west ham united": "west ham", "西汉姆联": "west ham",
        "wol": "wolves", "wolverhampton wanderers": "wolves", "狼队": "wolves",
    }
    text = str(value or "").strip().lower()
    for token in [" football club", " fc", " afc", " cf", ".", ","]:
        text = text.replace(token, " ")
    text = text.replace("&", " and ")
    text = " ".join(text.split())
    return aliases.get(text, text)


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
    fixture_matches = 0
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
            fixture_matches += 1
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
    if fixture_matches:
        try:
            updated = persist_fixture_metadata(matches, league_key)
            print(f"[API-Football] persisted fixture metadata: matched={fixture_matches}, updated={updated}, league={league_key}")
        except Exception as exc:
            print(f"[API-Football] persist fixture metadata failed: {exc}")
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


def compact_odds_history(snapshot_rows, limit=ODDS_HISTORY_DISPLAY_LIMIT):
    """Return a compact odds timeline, dropping repeated unchanged snapshots."""
    compacted = []
    seen = set()
    for row in snapshot_rows:
        key = (
            row.market,
            row.selection,
            row.point,
            row.source,
            row.bookmaker,
            row.price,
        )
        if key in seen:
            continue
        seen.add(key)
        compacted.append(odds_snapshot_payload(row))
        if len(compacted) >= limit:
            break
    return compacted


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


def match_row_frontend_payload(row):
    """Build the match shape used by the detail panel without reloading all fixtures."""
    kickoff = row.kickoff_time
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    cst = kickoff.astimezone(timezone(timedelta(hours=8)))
    league = "世界杯" if row.competition_key == "worldcup" else "英超"
    competition = "FIFA World Cup 2026" if row.competition_key == "worldcup" else "Premier League"
    return {
        "id": row.source_id,
        "league": league,
        "competition": competition,
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
        "status": row.status or match_status_from_kickoff(kickoff),
        "scoreH": row.score_home,
        "scoreW": row.score_away,
        "venue": row.venue,
        "city": row.city,
        "oddsAvailable": False,
        "pick": {"type": "pending", "label": "AI分析待接入", "conf": 0, "reason": "等待 AI 复评"},
    }


def db_match_detail_payload(league_key, source_id, plan):
    """Fast path for match details: one DB lookup instead of rebuilding /api/matches."""
    with session_scope() as session:
        row = session.scalar(
            select(Match).where(Match.competition_key == league_key, Match.source_id == source_id)
        )
        if not row:
            return None

        snapshot_rows = session.scalars(
            select(OddsSnapshot)
            .where(OddsSnapshot.match_id == row.id)
            .order_by(OddsSnapshot.captured_at.desc())
            .limit(ODDS_HISTORY_QUERY_LIMIT)
        ).all()
        rich_rows = session.scalars(
            select(MatchData)
            .where(MatchData.match_id == row.id)
            .order_by(MatchData.fetched_at.desc())
        ).all()
        analyses = [
            analysis_payload(item, plan)
            for item in session.scalars(
                select(AnalysisResult)
                .where(AnalysisResult.match_id == row.id)
                .order_by(AnalysisResult.created_at.desc())
                .limit(10)
            ).all()
        ]

        return {
            "match": match_row_frontend_payload(row),
            "league": league_key,
            "dbMatchId": row.id,
            "subscription": {"plan": plan},
            "oddsSources": current_odds_sources(snapshot_rows),
            "recentOddsBatch": recent_snapshot_batch(snapshot_rows),
            "oddsSnapshots": compact_odds_history(snapshot_rows),
            "matchData": match_data_payload(rich_rows),
            "analysisResults": analyses,
            "updated": cst_now().isoformat(),
        }


@app.route("/api/matches/<league_key>/<path:source_id>")
def match_detail(league_key, source_id):
    """Single match detail for the frontend detail panel."""
    if league_key not in {"worldcup", "epl"}:
        return jsonify({"error": "Unsupported league"}), 404
    plan = current_plan()
    try:
        db_payload = db_match_detail_payload(league_key, source_id, plan)
        if db_payload:
            return jsonify(db_payload)
    except Exception as exc:
        print(f"[DB] fast match detail failed: {exc}")

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
                    .limit(ODDS_HISTORY_QUERY_LIMIT)
                ).all()
                snapshots = compact_odds_history(snapshot_rows)
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
