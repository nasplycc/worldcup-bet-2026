from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from state import load_json, project_path


JQS_COLUMNS = ["jqs_0", "jqs_1", "jqs_2", "jqs_3", "jqs_4", "jqs_5", "jqs_6", "jqs_7p"]


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    return float(value)


def as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    return int(float(value))


def as_bool(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def split_notes(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.replace("|", "；").split("；") if part.strip()]


def load_json_matches(path: str | Path) -> list[dict[str, Any]]:
    return load_json(path, [])


def load_csv_matches(path: str | Path) -> list[dict[str, Any]]:
    p = project_path(path)
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    matches = []
    for row in rows:
        if not any((v or "").strip() for v in row.values()):
            continue

        rqspf = {}
        if row.get("rq_handicap") not in {None, ""}:
            rqspf = {
                "handicap": as_int(row.get("rq_handicap")),
                "3": as_float(row.get("rq_3")),
                "1": as_float(row.get("rq_1")),
                "0": as_float(row.get("rq_0")),
            }

        jqs = {}
        if any(row.get(col) not in {None, ""} for col in JQS_COLUMNS):
            jqs = {
                "0": as_float(row.get("jqs_0")),
                "1": as_float(row.get("jqs_1")),
                "2": as_float(row.get("jqs_2")),
                "3": as_float(row.get("jqs_3")),
                "4": as_float(row.get("jqs_4")),
                "5": as_float(row.get("jqs_5")),
                "6": as_float(row.get("jqs_6")),
                "7+": as_float(row.get("jqs_7p")),
            }

        odds: dict[str, Any] = {}
        if all(row.get(col) not in {None, ""} for col in ["spf_3", "spf_1", "spf_0"]):
            odds["spf"] = {
                "3": as_float(row.get("spf_3")),
                "1": as_float(row.get("spf_1")),
                "0": as_float(row.get("spf_0")),
            }
        if rqspf:
            odds["rqspf"] = rqspf
        if jqs:
            odds["jqs"] = jqs

        matches.append(
            {
                "match_id": row["match_id"].strip(),
                "competition": row.get("competition", "2026 World Cup").strip() or "2026 World Cup",
                "stage": row.get("stage", "").strip(),
                "kickoff_time": row["kickoff_time"].strip(),
                "home_team": row["home_team"].strip(),
                "away_team": row["away_team"].strip(),
                "neutral_ground": as_bool(row.get("neutral_ground"), True),
                "home_rating": as_float(row.get("home_rating")),
                "away_rating": as_float(row.get("away_rating")),
                "notes": split_notes(row.get("notes")),
                "odds": odds,
            }
        )

    return matches


def load_match_source(path: str | Path) -> list[dict[str, Any]]:
    p = project_path(path)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        return load_csv_matches(p)
    if suffix == ".json":
        return load_json_matches(p)
    raise ValueError(f"Unsupported match source: {p}")


def validate_matches(matches: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for idx, match in enumerate(matches, start=1):
        prefix = f"match[{idx}]"
        for field in ["match_id", "kickoff_time", "home_team", "away_team"]:
            if not match.get(field):
                errors.append(f"{prefix}: missing {field}")
        odds = match.get("odds", {})
        if not odds:
            continue
        spf = odds.get("spf", {})
        if spf:
            for key in ["3", "1", "0"]:
                if not spf.get(key):
                    errors.append(f"{prefix} {match.get('match_id', '')}: missing spf_{key}")
        for play in ["rqspf", "jqs"]:
            if play in odds and not odds[play]:
                errors.append(f"{prefix} {match.get('match_id', '')}: empty {play}")
    return errors


def first_existing_source(primary: str, fallback: str | None = None) -> tuple[str, bool]:
    primary_path = project_path(primary)
    if primary_path.exists():
        return primary, False
    if fallback and project_path(fallback).exists():
        return fallback, True
    return primary, False
