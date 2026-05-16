from __future__ import annotations

from typing import Any

from jingcai import recommend_jqs, recommend_rqspf, recommend_spf
from teams import team_profile


def analyze_match(
    match: dict[str, Any],
    config: dict[str, Any],
    plays: set[str],
    teams: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cfg = config.get("strategy", {})
    teams = teams or {}
    recommendations = []

    odds = match.get("odds", {})
    if "spf" in plays and odds.get("spf"):
        recommendations.append(recommend_spf(match, cfg).to_dict())
    if "rqspf" in plays and odds.get("rqspf"):
        recommendations.append(recommend_rqspf(match, cfg).to_dict())
    if "jqs" in plays and odds.get("jqs"):
        recommendations.append(recommend_jqs(match, cfg).to_dict())

    best = max(recommendations, key=lambda r: r["confidence"], default=None)
    return {
        "match_id": match["match_id"],
        "competition": match.get("competition", ""),
        "stage": match.get("stage", ""),
        "kickoff_time": match["kickoff_time"],
        "home_team": match["home_team"],
        "away_team": match["away_team"],
        "home_team_code": match.get("home_team_code", ""),
        "away_team_code": match.get("away_team_code", ""),
        "home_profile": team_profile(match["home_team"], match.get("home_team_code"), teams),
        "away_profile": team_profile(match["away_team"], match.get("away_team_code"), teams),
        "venue": match.get("venue", ""),
        "city": match.get("city", ""),
        "group": match.get("group", ""),
        "odds_available": bool(odds),
        "notes": match.get("notes", []),
        "recommendations": recommendations,
        "best_pick": best,
    }


def build_parlay(analyses: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = config.get("strategy", {})
    min_conf = cfg.get("parlay_min_confidence", 0.62)
    max_matches = cfg.get("max_parlay_matches", 4)

    candidates = []
    for analysis in analyses:
        spf = next((r for r in analysis["recommendations"] if r["play"] == "胜平负"), None)
        if not spf or spf["confidence"] < min_conf:
            continue
        candidates.append({
            "match_id": analysis["match_id"],
            "match": f"{analysis['home_team']} vs {analysis['away_team']}",
            "play": spf["play"],
            "pick": spf["picks"],
            "backup": spf["backup"],
            "confidence": spf["confidence"],
            "reason": spf["reason"],
        })

    candidates.sort(key=lambda item: item["confidence"], reverse=True)
    return candidates[:max_matches]
