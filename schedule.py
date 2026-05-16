from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from odds_sources import first_existing_source, load_match_source


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_matches(matches_file: str, fallback_file: str | None = None) -> tuple[list[dict[str, Any]], str, bool]:
    source, used_fallback = first_existing_source(matches_file, fallback_file)
    matches = load_match_source(source)
    return sorted(matches, key=lambda m: parse_dt(m["kickoff_time"])), source, used_fallback


def filter_matches(
    matches: list[dict[str, Any]],
    mode: str,
    days: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now().astimezone()
    if mode == "all":
        return matches

    if mode == "today":
        start = now.date()
        return [m for m in matches if parse_dt(m["kickoff_time"]).date() == start]

    if mode in {"upcoming", "parlay"}:
        end = now + timedelta(days=days)
        return [m for m in matches if now <= parse_dt(m["kickoff_time"]) <= end]

    return matches
