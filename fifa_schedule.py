from __future__ import annotations

from datetime import datetime
from typing import Any

import requests


FIFA_MATCHES_API = (
    "https://api.fifa.com/api/v3/calendar/matches"
    "?language=en&count=200&idCompetition=17&from=2026-06-01&to=2026-07-31"
)
FIFA_SCHEDULE_PAGE = (
    "https://www.fifa.com/en/tournaments/mens/worldcup/"
    "canadamexicousa2026/articles/match-schedule-fixtures-results-teams-stadiums"
)


def localized(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            if item.get("Locale") == "en-GB":
                return item.get("Description", default)
        if value:
            return value[0].get("Description", default)
    return default


def team_name(team: dict[str, Any] | None, placeholder: dict[str, Any] | None) -> str:
    if team:
        return localized(team.get("TeamName"), team.get("ShortClubName") or team.get("Abbreviation") or "TBD")
    if placeholder:
        if isinstance(placeholder, str):
            return placeholder
        return localized(placeholder.get("Name"), placeholder.get("IdPlaceholder") or "TBD")
    return "TBD"


def parse_date(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()


def convert_match(raw: dict[str, Any]) -> dict[str, Any]:
    stadium = raw.get("Stadium") or {}
    home = raw.get("Home")
    away = raw.get("Away")
    match_number = int(raw.get("MatchNumber") or raw.get("IdMatch") or 0)
    return {
        "match_id": f"fifa-2026-{match_number:03d}",
        "fifa_match_id": raw.get("IdMatch"),
        "match_number": match_number,
        "competition": localized(raw.get("SeasonName"), "FIFA World Cup 2026"),
        "stage": localized(raw.get("StageName")),
        "group": localized(raw.get("GroupName")),
        "kickoff_time": parse_date(raw["Date"]),
        "home_team": team_name(home, raw.get("PlaceHolderA")),
        "away_team": team_name(away, raw.get("PlaceHolderB")),
        "home_team_code": (home or {}).get("Abbreviation", ""),
        "away_team_code": (away or {}).get("Abbreviation", ""),
        "neutral_ground": True,
        "home_rating": 80,
        "away_rating": 80,
        "venue": localized(stadium.get("Name")),
        "city": localized(stadium.get("CityName")),
        "country": stadium.get("IdCountry", ""),
        "source": "FIFA API",
        "source_url": FIFA_MATCHES_API,
        "official_schedule_url": FIFA_SCHEDULE_PAGE,
        "odds": {},
        "notes": ["FIFA 官方赛程；竞彩赔率待开售"],
    }


def fetch_worldcup_2026_schedule() -> list[dict[str, Any]]:
    response = requests.get(FIFA_MATCHES_API, headers={"User-Agent": "Mozilla/5.0"}, timeout=40)
    response.raise_for_status()
    data = response.json()
    matches = [convert_match(item) for item in data.get("Results", [])]
    matches.sort(key=lambda m: (m["kickoff_time"], m["match_number"]))
    if len(matches) != 104:
        raise ValueError(f"Expected 104 FIFA matches, got {len(matches)}")
    return matches
