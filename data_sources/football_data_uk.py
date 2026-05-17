from __future__ import annotations

import csv
import io
import os
import time
from datetime import datetime, timezone, timedelta

import requests
from sqlalchemy import select

from db import Match, MatchData, OddsSnapshot, session_scope


ENABLED = os.environ.get("FOOTBALL_DATA_UK_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
EPL_CSV_URL = os.environ.get("FOOTBALL_DATA_UK_EPL_CSV", "https://www.football-data.co.uk/mmz4281/2526/E0.csv")
SYNC_LIMIT = max(0, int(os.environ.get("FOOTBALL_DATA_UK_SYNC_LIMIT", "420")))
CACHE = {}


def canonical_team(value):
    text = str(value or "").lower()
    for token in [" football club", " fc", " afc", " cf", ".", "&"]:
        text = text.replace(token, " ")
    text = text.replace(" and ", " ")
    return " ".join(text.split())


def team_alias(name):
    mapping = {
        "Arsenal": "arsenal",
        "Aston Villa": "aston villa",
        "Bournemouth": "bournemouth",
        "Brentford": "brentford",
        "Brighton": "brighton hove albion",
        "Burnley": "burnley",
        "Chelsea": "chelsea",
        "Crystal Palace": "crystal palace",
        "Everton": "everton",
        "Fulham": "fulham",
        "Leeds": "leeds united",
        "Liverpool": "liverpool",
        "Man City": "manchester city",
        "Man United": "manchester united",
        "Newcastle": "newcastle united",
        "Nott'm Forest": "nottingham forest",
        "Sunderland": "sunderland",
        "Tottenham": "tottenham hotspur",
        "West Ham": "west ham united",
        "Wolves": "wolverhampton wanderers",
    }
    return mapping.get(str(name or "").strip(), canonical_team(name))


def match_key(home, away, date):
    teams = sorted([team_alias(home), team_alias(away)])
    return f"{str(date or '')[:10]}|{teams[0]}|{teams[1]}"


def ordered_key(home, away):
    return f"{team_alias(home)}|{team_alias(away)}"


def decimal_or_none(value):
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_none(value):
    try:
        if value in {None, ""}:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_date(value):
    try:
        return datetime.strptime(str(value or "").strip(), "%d/%m/%Y").date().isoformat()
    except Exception:
        return ""


def fetch_rows(league_key="epl"):
    if not ENABLED or league_key != "epl":
        return []
    now = time.time()
    cache_key = f"football_data_uk_{league_key}"
    if cache_key in CACHE and now - CACHE[cache_key]["ts"] < 3600:
        return CACHE[cache_key]["data"]
    try:
        resp = requests.get(EPL_CSV_URL, timeout=20)
        if not resp.ok:
            print(f"[football-data.co.uk] {resp.status_code}: {resp.text[:200]}")
            return []
        text = resp.content.decode("utf-8-sig", errors="replace")
        rows = [
            row for row in csv.DictReader(io.StringIO(text))
            if row.get("Date") and row.get("HomeTeam") and row.get("AwayTeam")
        ]
        CACHE[cache_key] = {"data": rows, "ts": now}
        return rows
    except Exception as exc:
        print(f"[football-data.co.uk Error] {exc}")
        return []


def row_payload(row):
    stats = {
        "full_time": {
            "home_goals": int_or_none(row.get("FTHG")),
            "away_goals": int_or_none(row.get("FTAG")),
            "result": row.get("FTR") or "",
        },
        "half_time": {
            "home_goals": int_or_none(row.get("HTHG")),
            "away_goals": int_or_none(row.get("HTAG")),
            "result": row.get("HTR") or "",
        },
        "match_stats": {
            "home_shots": int_or_none(row.get("HS")),
            "away_shots": int_or_none(row.get("AS")),
            "home_shots_on_target": int_or_none(row.get("HST")),
            "away_shots_on_target": int_or_none(row.get("AST")),
            "home_fouls": int_or_none(row.get("HF")),
            "away_fouls": int_or_none(row.get("AF")),
            "home_corners": int_or_none(row.get("HC")),
            "away_corners": int_or_none(row.get("AC")),
            "home_yellow_cards": int_or_none(row.get("HY")),
            "away_yellow_cards": int_or_none(row.get("AY")),
            "home_red_cards": int_or_none(row.get("HR")),
            "away_red_cards": int_or_none(row.get("AR")),
        },
        "referee": row.get("Referee") or "",
    }
    odds = {
        "h2h": {
            "home": decimal_or_none(row.get("AvgCH") or row.get("AvgH")),
            "draw": decimal_or_none(row.get("AvgCD") or row.get("AvgD")),
            "away": decimal_or_none(row.get("AvgCA") or row.get("AvgA")),
        },
        "totals": [
            {"name": "over", "point": 2.5, "price": decimal_or_none(row.get("AvgC>2.5") or row.get("Avg>2.5"))},
            {"name": "under", "point": 2.5, "price": decimal_or_none(row.get("AvgC<2.5") or row.get("Avg<2.5"))},
        ],
        "spreads": [{
            "point": decimal_or_none(row.get("AHCh") or row.get("AHh")),
            "home": decimal_or_none(row.get("AvgCAHH") or row.get("AvgAHH")),
            "away": decimal_or_none(row.get("AvgCAHA") or row.get("AvgAHA")),
        }],
    }
    odds["h2h"] = {key: value for key, value in odds["h2h"].items() if value is not None}
    odds["totals"] = [item for item in odds["totals"] if item["price"] is not None]
    odds["spreads"] = [
        item for item in odds["spreads"]
        if item["point"] is not None and (item["home"] is not None or item["away"] is not None)
    ]
    return {
        "provider": "football-data.co.uk",
        "competition": row.get("Div") or "E0",
        "date": parse_date(row.get("Date")),
        "time": row.get("Time") or "",
        "homeTeam": row.get("HomeTeam") or "",
        "awayTeam": row.get("AwayTeam") or "",
        "stats": stats,
        "odds": odds,
        "raw": row,
    }


def upsert_match_data(session, match_id, payload):
    row = session.scalar(
        select(MatchData).where(
            MatchData.match_id == match_id,
            MatchData.data_type == "historical_stats",
            MatchData.source == "football-data.co.uk",
        )
    )
    if row:
        row.payload = payload
        row.fetched_at = datetime.now(timezone.utc)
    else:
        session.add(MatchData(
            match_id=match_id,
            data_type="historical_stats",
            source="football-data.co.uk",
            payload=payload,
        ))


def add_snapshots(session, match, payload):
    odds = payload.get("odds") or {}
    existing = session.scalars(
        select(OddsSnapshot).where(
            OddsSnapshot.match_id == match.id,
            OddsSnapshot.competition_key == match.competition_key,
            OddsSnapshot.source == "football-data.co.uk",
        )
    ).all()
    existing_set = {
        (row.market, row.selection, row.point, round(float(row.price), 4))
        for row in existing
    }
    inserted = 0
    captured_at = datetime.now(timezone.utc)

    def add_snapshot(market, selection, price, point=None):
        nonlocal inserted
        if price is None:
            return
        key = (market, selection, point, round(float(price), 4))
        if key in existing_set:
            return
        session.add(OddsSnapshot(
            match_id=match.id,
            competition_key=match.competition_key,
            source="football-data.co.uk",
            bookmaker="Average closing",
            market=market,
            selection=selection,
            point=point,
            price=float(price),
            captured_at=captured_at,
            raw=payload,
        ))
        inserted += 1

    for selection, price in (odds.get("h2h") or {}).items():
        add_snapshot("h2h", selection, price)
    for item in odds.get("totals") or []:
        add_snapshot("totals", str(item.get("name") or ""), item.get("price"), item.get("point"))
    for item in odds.get("spreads") or []:
        point = item.get("point")
        for selection in ("home", "away"):
            add_snapshot("spread", selection, item.get(selection), point)
    return inserted


def sync_match_data(limit=None):
    if not ENABLED:
        return {"enabled": False, "synced": 0, "reason": "FOOTBALL_DATA_UK_ENABLED is false"}
    limit = SYNC_LIMIT if limit is None else int(limit)
    if limit <= 0:
        return {"enabled": True, "synced": 0, "reason": "FOOTBALL_DATA_UK_SYNC_LIMIT is 0"}

    rows = fetch_rows("epl")
    if not rows:
        return {"enabled": True, "synced": 0, "skipped": 0, "reason": "no football-data.co.uk rows returned"}

    row_by_key = {}
    row_by_ordered_key = {}
    for row in rows:
        date = parse_date(row.get("Date"))
        if not date:
            continue
        row_by_key[match_key(row.get("HomeTeam"), row.get("AwayTeam"), date)] = row
        row_by_ordered_key.setdefault(ordered_key(row.get("HomeTeam"), row.get("AwayTeam")), []).append(row)

    synced = 0
    snapshots = 0
    skipped = 0
    with session_scope() as session:
        matches = session.scalars(
            select(Match)
            .where(Match.competition_key == "epl")
            .order_by(Match.kickoff_time.desc())
            .limit(420)
        ).all()
        for match in matches:
            if synced >= limit:
                break
            kickoff = match.kickoff_time
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
            match_date = kickoff.astimezone(timezone(timedelta(hours=8))).date().isoformat()
            row = row_by_key.get(match_key(
                match.home_team_name or match.home_team_code,
                match.away_team_name or match.away_team_code,
                match_date,
            ))
            if not row:
                row = nearest_ordered_row(
                    row_by_ordered_key,
                    match.home_team_name or match.home_team_code,
                    match.away_team_name or match.away_team_code,
                    match_date,
                )
            if not row:
                skipped += 1
                continue

            payload = row_payload(row)
            ft = (payload.get("stats") or {}).get("full_time") or {}
            if ft.get("home_goals") is not None:
                match.score_home = ft.get("home_goals")
            if ft.get("away_goals") is not None:
                match.score_away = ft.get("away_goals")
            if match.score_home is not None and match.score_away is not None:
                match.status = "finished"
            raw = dict(match.raw or {})
            raw.update({"footballDataUk": payload})
            match.raw = raw
            upsert_match_data(session, match.id, payload)
            snapshots += add_snapshots(session, match, payload)
            synced += 1
    return {"enabled": True, "synced": synced, "snapshots": snapshots, "skipped": skipped}


def nearest_ordered_row(row_by_ordered_key, home, away, match_date):
    candidates = row_by_ordered_key.get(ordered_key(home, away)) or []
    nearest = None
    nearest_days = 999
    for candidate in candidates:
        candidate_date = parse_date(candidate.get("Date"))
        if not candidate_date:
            continue
        days = abs((datetime.fromisoformat(match_date) - datetime.fromisoformat(candidate_date)).days)
        if days < nearest_days:
            nearest = candidate
            nearest_days = days
    return nearest if nearest is not None and nearest_days <= 10 else None
