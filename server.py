"""
Flask backend for worldcup-bet-2026.

NAS deployment serves the frontend and World Cup analysis API from this process.
"""
from dotenv import load_dotenv
load_dotenv()
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from flask import Flask, jsonify, has_request_context, request, send_from_directory

from frontend_data import merge_worldcup_frontend, recommendations_to_frontend, schedule_to_frontend
from state import load_json

app = Flask(__name__, static_folder="docs", static_url_path="")
ROOT = Path(__file__).resolve().parent

# Config
API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
BASE = "https://api.football-data.org/v4"
OPENFOOTBALL_EPL_2025_26 = "https://openfootball.github.io/england/2025-26/1-premierleague.json"
CACHE = {}
CACHE_TTL = 60


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
    schedule_payload = load_json("data/worldcup_2026_schedule.json", [])
    schedule_frontend = schedule_to_frontend(schedule_payload) if schedule_payload else {}

    payload = load_json("data/recommendations.json", {})
    if payload:
        recommendations_frontend = recommendations_to_frontend(payload)
        if schedule_frontend:
            return merge_worldcup_frontend(schedule_frontend, recommendations_frontend), 200
        return recommendations_frontend, 200

    if schedule_frontend:
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


@app.route("/api/health")
def health():
    worldcup_payload, worldcup_status = load_worldcup_frontend_payload()
    epl_payload, epl_status = load_epl_frontend_payload()
    return jsonify({
        "ok": worldcup_status == 200 or epl_status == 200,
        "football_data_api_key": bool(API_KEY),
        "epl_matches": epl_payload.get("count", 0),
        "epl_status": epl_status,
        "worldcup_matches": worldcup_payload.get("count", 0),
        "worldcup_status": worldcup_status,
        "source": "combined",
        "updated": cst_now().isoformat(),
    }), 200


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
    app.run(host="0.0.0.0", port=8088, debug=False, threaded=True)
