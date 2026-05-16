from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from state import load_json, save_json


PLAY_PREFIX = {
    "胜平负": "spf",
    "让球胜平负": "rqspf",
    "总进球": "jqs",
}


def flatten_recommendation_odds(item: dict[str, Any]) -> dict[str, float]:
    flattened: dict[str, float] = {}
    for rec in item.get("recommendations", []):
        prefix = PLAY_PREFIX.get(rec.get("play", ""), rec.get("play", "unknown"))
        odds = rec.get("odds", {})
        for key, value in odds.items():
            if key == "handicap":
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                flattened[f"{prefix}.{key}"] = number
    return flattened


def movement_summary(changes: list[dict[str, Any]]) -> str:
    if not changes:
        return "赔率暂无变化"
    up = sum(1 for change in changes if change["direction"] == "up")
    down = sum(1 for change in changes if change["direction"] == "down")
    significant = sum(1 for change in changes if change.get("significant"))
    return f"赔率变化 {len(changes)} 项，上调 {up} 项，下调 {down} 项，显著变化 {significant} 项"


def compare_odds(
    current: dict[str, float],
    previous: dict[str, float],
    absolute_threshold: float,
    percent_threshold: float,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for key, new_value in sorted(current.items()):
        old_value = previous.get(key)
        if old_value is None:
            changes.append(
                {
                    "market": key,
                    "old": None,
                    "new": round(new_value, 3),
                    "delta": None,
                    "percent": None,
                    "direction": "new",
                    "significant": True,
                }
            )
            continue
        delta = new_value - old_value
        if abs(delta) < 0.001:
            continue
        percent = delta / old_value if old_value else 0
        changes.append(
            {
                "market": key,
                "old": round(old_value, 3),
                "new": round(new_value, 3),
                "delta": round(delta, 3),
                "percent": round(percent, 4),
                "direction": "up" if delta > 0 else "down",
                "significant": abs(delta) >= absolute_threshold or abs(percent) >= percent_threshold,
            }
        )
    return changes


def attach_odds_movements(analyses: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    tracking_cfg = config.get("odds_tracking", {})
    if not tracking_cfg.get("enabled", True):
        return {"enabled": False, "changes": []}

    data_cfg = config.get("data", {})
    snapshot_file = data_cfg.get("odds_snapshot_file", "data/odds_snapshot.json")
    movements_file = data_cfg.get("odds_movements_file", "data/odds_movements.json")
    absolute_threshold = float(tracking_cfg.get("absolute_threshold", 0.05))
    percent_threshold = float(tracking_cfg.get("percent_threshold", 0.03))

    now = datetime.now(timezone.utc).isoformat()
    previous_snapshot = load_json(snapshot_file, {})
    previous_matches = previous_snapshot.get("matches", {})

    current_matches: dict[str, Any] = {}
    movement_events: list[dict[str, Any]] = []

    for item in analyses:
        match_id = item.get("match_id", "")
        current_odds = flatten_recommendation_odds(item)
        previous_odds = previous_matches.get(match_id, {}).get("odds", {})

        if not current_odds:
            item["odds_movement"] = {
                "status": "no_odds",
                "summary": "竞彩赔率待开售，无法做赔率变化追踪",
                "changes": [],
            }
            continue

        changes = compare_odds(current_odds, previous_odds, absolute_threshold, percent_threshold)
        status = "new_match" if not previous_odds else ("changed" if changes else "unchanged")
        significant_changes = [change for change in changes if change.get("significant")]

        movement = {
            "status": status,
            "summary": movement_summary(changes),
            "changes": changes,
            "significant_changes": significant_changes,
        }
        item["odds_movement"] = movement

        if significant_changes:
            movement_events.append(
                {
                    "match_id": match_id,
                    "match": f"{item.get('home_team', '')} vs {item.get('away_team', '')}",
                    "kickoff_time": item.get("kickoff_time", ""),
                    "status": status,
                    "summary": movement["summary"],
                    "significant_changes": significant_changes,
                }
            )

        current_matches[match_id] = {
            "match": f"{item.get('home_team', '')} vs {item.get('away_team', '')}",
            "kickoff_time": item.get("kickoff_time", ""),
            "odds": current_odds,
            "updated_at": now,
        }

    snapshot_matches = dict(previous_matches)
    snapshot_matches.update(current_matches)
    snapshot = {
        "generated_at": now,
        "matches": snapshot_matches,
    }
    save_json(snapshot_file, snapshot)

    movement_report = {
        "generated_at": now,
        "absolute_threshold": absolute_threshold,
        "percent_threshold": percent_threshold,
        "changes": movement_events,
    }
    save_json(movements_file, movement_report)
    return movement_report
