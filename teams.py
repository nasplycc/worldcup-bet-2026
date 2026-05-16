from __future__ import annotations

from typing import Any

from state import load_json


def normalize_key(value: str | None) -> str:
    return (value or "").strip().lower()


def load_teams(path: str) -> dict[str, dict[str, Any]]:
    rows = load_json(path, [])
    teams: dict[str, dict[str, Any]] = {}
    for row in rows:
        keys = [row.get("name"), row.get("code"), *row.get("aliases", [])]
        for key in keys:
            if key:
                teams[normalize_key(key)] = row
    return teams


def team_profile(team_name: str, team_code: str | None, teams: dict[str, dict[str, Any]]) -> dict[str, Any]:
    profile = teams.get(normalize_key(team_code)) or teams.get(normalize_key(team_name))
    if profile:
        return profile
    return {
        "name": team_name,
        "code": team_code or "",
        "tier": "unknown",
        "fifa_rank_estimate": None,
        "elo_estimate": None,
        "attack": 50,
        "defense": 50,
        "tournament_experience": 50,
        "squad_depth": 50,
        "upset_potential": 50,
        "volatility": 50,
        "tags": ["needs_research"],
        "notes": "No local team profile yet. OpenClaw should research current news and squad data.",
    }
