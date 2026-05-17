from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta

import requests
from sqlalchemy import select

from db import Match, MatchData, OddsSnapshot, session_scope


ENABLED = os.environ.get("SPORTTERY_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
BASE = os.environ.get("SPORTTERY_BASE", "https://webapi.sporttery.cn/gateway").rstrip("/")
SYNC_LIMIT = max(0, int(os.environ.get("SPORTTERY_SYNC_LIMIT", "20")))
CACHE = {}


def canonical_team(value):
    text = str(value or "").lower()
    for token in [" football club", " fc", " afc", " cf", ".", "&"]:
        text = text.replace(token, " ")
    text = text.replace(" and ", " ")
    return " ".join(text.split())


def league_names(league_key):
    mapping = {
        "epl": {"英超", "英格兰超级联赛", "英格兰超级"},
        "worldcup": {"世界杯", "世俱杯", "FIFA世界杯"},
    }
    return mapping.get(league_key, set())


def team_alias(name):
    mapping = {
        "阿森纳": "arsenal",
        "维拉": "aston villa",
        "阿斯顿维拉": "aston villa",
        "伯恩茅斯": "bournemouth",
        "布伦特": "brentford",
        "布伦特福德": "brentford",
        "布赖顿": "brighton hove albion",
        "布莱顿": "brighton hove albion",
        "伯恩利": "burnley",
        "切尔西": "chelsea",
        "水晶宫": "crystal palace",
        "埃弗顿": "everton",
        "富勒姆": "fulham",
        "利兹联": "leeds united",
        "利物浦": "liverpool",
        "曼城": "manchester city",
        "曼联": "manchester united",
        "曼彻斯特联": "manchester united",
        "曼彻斯特城": "manchester city",
        "纽卡斯尔": "newcastle united",
        "纽卡": "newcastle united",
        "纽卡斯尔联": "newcastle united",
        "诺丁汉": "nottingham forest",
        "诺丁汉森林": "nottingham forest",
        "桑德兰": "sunderland",
        "热刺": "tottenham hotspur",
        "西汉姆": "west ham united",
        "西汉姆联": "west ham united",
        "狼队": "wolverhampton wanderers",
    }
    text = str(name or "").strip()
    return mapping.get(text, canonical_team(text))


def team_key(home, away, date):
    teams = sorted([team_alias(home), team_alias(away)])
    return f"{str(date or '')[:10]}|{teams[0]}|{teams[1]}"


def fetch_with_cache(key, path, ttl=300):
    if not ENABLED:
        return None
    now = time.time()
    if key in CACHE and now - CACHE[key]["ts"] < ttl:
        return CACHE[key]["data"]
    url = f"{BASE}/{path.lstrip('/')}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.sporttery.cn/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if not resp.ok:
            print(f"[Sporttery] {path} -> {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        if not data.get("success"):
            print(f"[Sporttery] {path} -> {data.get('errorCode')}: {data.get('errorMessage')}")
            return None
        CACHE[key] = {"data": data, "ts": now}
        return data
    except Exception as exc:
        print(f"[Sporttery Error] {exc}")
        return None


def flatten_matches(data):
    rows = []
    value = data.get("value") or {}
    for group in value.get("matchInfoList") or []:
        for item in group.get("subMatchList") or []:
            if not item.get("isHide"):
                rows.append(item)
    return rows


def fetch_matches():
    path = "jc/football/getMatchCalculatorV1.qry?poolCode=hhad,had&channel=c"
    data = fetch_with_cache("sporttery_match_calculator", path, ttl=300)
    return flatten_matches(data) if isinstance(data, dict) else []


def price(value):
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def captured_at(pool):
    date = pool.get("updateDate") or ""
    time_text = pool.get("updateTime") or ""
    if date and time_text:
        try:
            return datetime.fromisoformat(f"{date}T{time_text}+08:00").astimezone(timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def item_datetime(item):
    date = item.get("matchDate") or item.get("businessDate") or ""
    time_text = item.get("matchTime") or "00:00"
    try:
        return datetime.fromisoformat(f"{date}T{time_text}:00+08:00")
    except Exception:
        return None


def odds_from_item(item):
    had = item.get("had") or {}
    hhad = item.get("hhad") or {}
    h2h = {
        "home": price(had.get("h")),
        "draw": price(had.get("d")),
        "away": price(had.get("a")),
    }
    h2h = {key: value for key, value in h2h.items() if value is not None}
    spread = {
        "point": price(hhad.get("goalLineValue") or hhad.get("goalLine")),
        "home": price(hhad.get("h")),
        "draw": price(hhad.get("d")),
        "away": price(hhad.get("a")),
    }
    if not any(value is not None for key, value in spread.items() if key != "point"):
        spread = {}
    updated = ""
    if had.get("updateDate") and had.get("updateTime"):
        updated = f"{had.get('updateDate')}T{had.get('updateTime')}+08:00"
    elif hhad.get("updateDate") and hhad.get("updateTime"):
        updated = f"{hhad.get('updateDate')}T{hhad.get('updateTime')}+08:00"
    return {
        "source": "sporttery",
        "bookmaker": "Sporttery",
        "updated": updated,
        "matchNum": item.get("matchNumStr") or item.get("matchNum"),
        "league": item.get("leagueAbbName") or item.get("leagueAllName") or "",
        "h2h": h2h,
        "spreads": [spread] if spread else [],
        "raw": item,
    }


def fetch_odds(league_key):
    names = league_names(league_key)
    if not names:
        return {}
    odds_by_match = {}
    for item in fetch_matches():
        league = str(item.get("leagueAbbName") or item.get("leagueAllName") or "")
        if league not in names:
            continue
        home = item.get("homeTeamAllName") or item.get("homeTeamAbbName") or ""
        away = item.get("awayTeamAllName") or item.get("awayTeamAbbName") or ""
        date = item.get("matchDate") or item.get("businessDate") or ""
        if home and away and date:
            odds_by_match[team_key(home, away, date)] = odds_from_item(item)
    return odds_by_match


def upsert_official_market(session, match, item, odds):
    row = session.scalar(
        select(MatchData).where(
            MatchData.match_id == match.id,
            MatchData.data_type == "official_market",
            MatchData.source == "sporttery",
        )
    )
    payload = {"provider": "Sporttery", "fixture": item, "odds": odds}
    if row:
        row.payload = payload
        row.fetched_at = datetime.now(timezone.utc)
    else:
        session.add(MatchData(match_id=match.id, data_type="official_market", source="sporttery", payload=payload))


def add_snapshots(session, match, odds):
    existing = session.scalars(
        select(OddsSnapshot).where(
            OddsSnapshot.match_id == match.id,
            OddsSnapshot.competition_key == match.competition_key,
            OddsSnapshot.source == "sporttery",
        )
    ).all()
    existing_set = {
        (row.market, row.selection, row.point, round(float(row.price), 4))
        for row in existing
    }
    inserted = 0
    raw = odds.get("raw") or {}
    had = raw.get("had") or {}
    hhad = raw.get("hhad") or {}

    def add_snapshot(market, selection, value, point=None, captured=None):
        nonlocal inserted
        if value is None:
            return
        key = (market, selection, point, round(float(value), 4))
        if key in existing_set:
            return
        session.add(OddsSnapshot(
            match_id=match.id,
            competition_key=match.competition_key,
            source="sporttery",
            bookmaker="Sporttery",
            market=market,
            selection=selection,
            price=float(value),
            point=point,
            captured_at=captured or datetime.now(timezone.utc),
            raw=odds,
        ))
        inserted += 1

    for selection, value in (odds.get("h2h") or {}).items():
        add_snapshot("h2h", selection, value, captured=captured_at(had))
    for spread in odds.get("spreads") or []:
        point = spread.get("point")
        for selection in ("home", "draw", "away"):
            add_snapshot("spread", selection, spread.get(selection), point=point, captured=captured_at(hhad))
    return inserted


def sync_match_data(limit=None):
    if not ENABLED:
        return {"enabled": False, "synced": 0, "reason": "SPORTTERY_ENABLED is false"}
    limit = SYNC_LIMIT if limit is None else int(limit)
    if limit <= 0:
        return {"enabled": True, "synced": 0, "reason": "SPORTTERY_SYNC_LIMIT is 0"}

    items = fetch_matches()
    if not items:
        return {"enabled": True, "synced": 0, "skipped": 0, "reason": "no public Sporttery matches returned"}

    public_by_key = {}
    for item in items:
        home = item.get("homeTeamAllName") or item.get("homeTeamAbbName") or ""
        away = item.get("awayTeamAllName") or item.get("awayTeamAbbName") or ""
        date = item.get("matchDate") or item.get("businessDate") or ""
        if home and away and date:
            public_by_key[team_key(home, away, date)] = item

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=7)
    end = now + timedelta(days=14)
    synced = 0
    snapshots = 0
    skipped = 0
    with session_scope() as session:
        rows = session.scalars(
            select(Match)
            .where(Match.kickoff_time >= start, Match.kickoff_time <= end)
            .order_by(Match.kickoff_time.asc())
            .limit(240)
        ).all()
        for match in rows:
            if synced >= limit:
                break
            kickoff = match.kickoff_time
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
            key = team_key(
                match.home_team_name or match.home_team_code,
                match.away_team_name or match.away_team_code,
                kickoff.astimezone(timezone(timedelta(hours=8))).date().isoformat(),
            )
            item = public_by_key.get(key)
            if not item:
                skipped += 1
                continue
            odds = odds_from_item(item)
            raw = dict(match.raw or {})
            raw.update({
                "sportteryMatchId": item.get("matchId"),
                "sportteryMatchNum": item.get("matchNumStr") or item.get("matchNum"),
                "sportteryLeague": item.get("leagueAbbName") or item.get("leagueAllName") or "",
                "sportteryFixture": item,
            })
            match.raw = raw
            kickoff_cst = item_datetime(item)
            if kickoff_cst:
                match.kickoff_time = kickoff_cst.astimezone(timezone.utc)
            upsert_official_market(session, match, item, odds)
            snapshots += add_snapshots(session, match, odds)
            synced += 1
    return {"enabled": True, "synced": synced, "snapshots": snapshots, "skipped": skipped}
