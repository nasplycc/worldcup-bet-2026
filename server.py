"""
Flask backend for worldcup-bet-2026.

NAS deployment serves the frontend and World Cup analysis API from this process.
"""
from dotenv import load_dotenv
load_dotenv()
import os
import time
import jwt
import threading
from urllib.parse import urlencode
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from flask import Flask, jsonify, has_request_context, request, send_from_directory
from sqlalchemy import select

from frontend_data import merge_worldcup_frontend, recommendations_to_frontend, schedule_to_frontend
from state import load_json
from db import AnalysisJob, AnalysisResult, Match, OddsSnapshot, Subscription, SyncRun, User, UserPreference, db_counts, init_db, password_hash, seed_all, session_scope
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
CACHE = {}
CACHE_TTL = 60
AUTO_SYNC_ENABLED = os.environ.get("AUTO_SYNC_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
AUTO_SYNC_INTERVAL_MINUTES = max(5, int(os.environ.get("AUTO_SYNC_INTERVAL_MINUTES", "60")))
SYNC_STARTUP_DELAY_SECONDS = max(0, int(os.environ.get("SYNC_STARTUP_DELAY_SECONDS", "20")))
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
    return fetch_api_football_odds(league_key)


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
        execute_sync(trigger="scheduled")
        time.sleep(AUTO_SYNC_INTERVAL_MINUTES * 60)


def start_scheduler_once():
    global SCHEDULER_STARTED
    if SCHEDULER_STARTED or not AUTO_SYNC_ENABLED:
        return
    SCHEDULER_STARTED = True
    thread = threading.Thread(target=scheduler_loop, name="data-sync-scheduler", daemon=True)
    thread.start()
    print(f"[Sync] scheduler enabled: every {AUTO_SYNC_INTERVAL_MINUTES} minutes")


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
    analyses = []
    db_match_id = None
    try:
        with session_scope() as session:
            row = session.scalar(
                select(Match).where(Match.competition_key == league_key, Match.source_id == source_id)
            )
            if row:
                db_match_id = row.id
                snapshots = [
                    odds_snapshot_payload(item)
                    for item in session.scalars(
                        select(OddsSnapshot)
                        .where(OddsSnapshot.match_id == row.id)
                        .order_by(OddsSnapshot.captured_at.desc())
                        .limit(60)
                    ).all()
                ]
                analyses = [
                    analysis_payload(item, plan)
                    for item in session.scalars(
                        select(AnalysisResult)
                        .where(AnalysisResult.match_id == row.id)
                        .order_by(AnalysisResult.created_at.desc())
                        .limit(10)
                    ).all()
                ]
    except Exception as exc:
        print(f"[DB] match detail failed: {exc}")

    return jsonify({
        "match": match,
        "league": league_key,
        "dbMatchId": db_match_id,
        "subscription": {"plan": plan},
        "oddsSnapshots": snapshots,
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
