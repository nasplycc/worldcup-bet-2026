from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def match_status(kickoff_time: str) -> str:
    now = datetime.now(timezone(timedelta(hours=8)))
    kickoff = parse_time(kickoff_time).astimezone(timezone(timedelta(hours=8)))
    if now < kickoff:
        return "upcoming"
    if now <= kickoff + timedelta(hours=2):
        return "live"
    return "finished"


def best_frontend_pick(item: dict[str, Any]) -> dict[str, Any]:
    pack = item.get("openclaw_analysis") or {}
    candidates = pack.get("candidate_bets") or []
    if candidates:
        pick = candidates[0]
        odds = pick.get("odds")
        odds_text = f" @{float(odds):.2f}" if isinstance(odds, (int, float)) else ""
        return {
            "type": "candidate",
            "label": f"{pick.get('play', '')} {pick.get('pick', '')}{odds_text}",
            "conf": min(float(pick.get("candidate_score", 0)) / 100, 0.95),
            "reason": pick.get("rule_reason") or "OpenClaw 候选池高价值方向",
            "score": pick.get("candidate_score", 0),
            "tags": pick.get("strategy_tags", []),
        }

    best = item.get("best_pick") or {}
    picks = "/".join(best.get("picks", []))
    if picks:
        return {
            "type": "rule",
            "label": f"{best.get('play', '')} {picks}",
            "conf": float(best.get("confidence", 0)),
            "reason": best.get("reason", ""),
            "score": round(float(best.get("confidence", 0)) * 100, 1),
            "tags": [best.get("risk_level", "")],
        }

    return {
        "type": "pending",
        "label": "赔率待开售",
        "conf": 0,
        "reason": "当前只有赛程数据，暂不生成正式投注号码",
        "score": 0,
        "tags": [],
    }


def recommendation_to_frontend(item: dict[str, Any]) -> dict[str, Any]:
    kickoff = parse_time(item["kickoff_time"]).astimezone(timezone(timedelta(hours=8)))
    home_profile = item.get("home_profile") or {}
    away_profile = item.get("away_profile") or {}
    status = match_status(item["kickoff_time"])
    return {
        "id": item.get("match_id", ""),
        "league": "世界杯",
        "competition": item.get("competition", "2026 World Cup"),
        "stage": item.get("stage", ""),
        "group": item.get("group", ""),
        "date": kickoff.strftime("%Y-%m-%d"),
        "time": kickoff.strftime("%H:%M"),
        "matchday": item.get("match_number") or "",
        "home": home_profile.get("code") or item.get("home_team", ""),
        "homeName": item.get("home_team", ""),
        "homeFull": home_profile.get("name") or item.get("home_team", ""),
        "away": away_profile.get("code") or item.get("away_team", ""),
        "awayName": item.get("away_team", ""),
        "awayFull": away_profile.get("name") or item.get("away_team", ""),
        "status": status,
        "minute": "" if status == "upcoming" else "进行中",
        "scoreH": None,
        "scoreW": None,
        "oddsAvailable": bool(item.get("odds_available")),
        "oddsMovement": item.get("odds_movement", {}),
        "pick": best_frontend_pick(item),
    }


def schedule_match_to_frontend(item: dict[str, Any]) -> dict[str, Any]:
    kickoff = parse_time(item["kickoff_time"]).astimezone(timezone(timedelta(hours=8)))
    status = match_status(item["kickoff_time"])
    return {
        "id": item.get("match_id", ""),
        "league": "世界杯",
        "competition": item.get("competition", "FIFA World Cup 2026"),
        "stage": item.get("stage", ""),
        "group": item.get("group", ""),
        "date": kickoff.strftime("%Y-%m-%d"),
        "time": kickoff.strftime("%H:%M"),
        "matchday": item.get("match_number") or "",
        "home": item.get("home_team_code") or item.get("home_team", ""),
        "homeName": item.get("home_team", ""),
        "homeFull": item.get("home_team", ""),
        "away": item.get("away_team_code") or item.get("away_team", ""),
        "awayName": item.get("away_team", ""),
        "awayFull": item.get("away_team", ""),
        "status": status,
        "minute": "" if status == "upcoming" else "进行中",
        "scoreH": None,
        "scoreW": None,
        "venue": item.get("venue", ""),
        "city": item.get("city", ""),
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
    }


def recommendations_to_frontend(payload: dict[str, Any]) -> dict[str, Any]:
    matches = [recommendation_to_frontend(item) for item in payload.get("recommendations", [])]
    return {
        "count": len(matches),
        "matches": matches,
        "source": payload.get("source_file", "data/recommendations.json"),
        "updated": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "mode": payload.get("mode", ""),
        "days": payload.get("days", ""),
    }


def schedule_to_frontend(matches_payload: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [schedule_match_to_frontend(item) for item in matches_payload]
    return {
        "count": len(matches),
        "matches": matches,
        "source": "data/worldcup_2026_schedule.json",
        "updated": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "mode": "official_schedule",
        "days": "",
    }


def match_key(item: dict[str, Any]) -> tuple[str, str]:
    home = str(item.get("home") or item.get("homeName") or "").strip().lower()
    away = str(item.get("away") or item.get("awayName") or "").strip().lower()
    return home, away


def merge_worldcup_frontend(schedule_payload: dict[str, Any], recommendations_payload: dict[str, Any]) -> dict[str, Any]:
    base_matches = list(schedule_payload.get("matches", []))
    overlay_matches = list(recommendations_payload.get("matches", []))
    merged_by_key = {match_key(item): item for item in base_matches}

    for item in overlay_matches:
        key = match_key(item)
        if key in merged_by_key:
            official = merged_by_key[key]
            merged_by_key[key] = {
                **official,
                **item,
                "matchday": official.get("matchday") or item.get("matchday", ""),
                "stage": official.get("stage") or item.get("stage", ""),
                "group": official.get("group") or item.get("group", ""),
                "venue": official.get("venue", ""),
                "city": official.get("city", ""),
            }
        elif not base_matches:
            merged_by_key[key] = item

    merged = list(merged_by_key.values())
    merged.sort(key=lambda row: (row.get("date", ""), row.get("time", ""), str(row.get("matchday", ""))))
    return {
        "count": len(merged),
        "matches": merged,
        "source": f"{schedule_payload.get('source', '')} + {recommendations_payload.get('source', '')}",
        "updated": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "mode": "official_schedule_with_recommendations",
        "days": recommendations_payload.get("days", ""),
    }
