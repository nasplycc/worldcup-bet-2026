from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from sqlalchemy import select

from db import AnalysisJob, AnalysisResult, Match, MatchData, OddsSnapshot, session_scope


PROMPT_VERSION = os.environ.get("AI_ANALYSIS_PROMPT_VERSION", "v1")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
API_KEY = os.environ.get("OPENAI_API_KEY", "")
AI_ENABLED = os.environ.get("AI_ANALYSIS_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
AI_LOOKAHEAD_HOURS = int(os.environ.get("AI_ANALYSIS_LOOKAHEAD_HOURS", "168"))
AI_MAX_JOBS_PER_SYNC = int(os.environ.get("AI_ANALYSIS_MAX_JOBS_PER_SYNC", "3"))
AI_DAILY_JOB_LIMIT = int(os.environ.get("AI_ANALYSIS_DAILY_JOB_LIMIT", "20"))
AI_MAX_RETRIES = int(os.environ.get("AI_ANALYSIS_MAX_RETRIES", "2"))
AI_CONTEXT_MAX_CHARS_PER_TYPE = int(os.environ.get("AI_CONTEXT_MAX_CHARS_PER_TYPE", "3500"))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def latest_odds_for_match(session, match_id: int) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(OddsSnapshot)
        .where(OddsSnapshot.match_id == match_id)
        .order_by(OddsSnapshot.captured_at.desc())
        .limit(12)
    ).all()
    seen = set()
    items = []
    for row in rows:
        key = (row.market, row.selection, row.point)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "market": row.market,
            "selection": row.selection,
            "price": row.price,
            "point": row.point,
            "source": row.source,
            "bookmaker": row.bookmaker,
            "captured_at": row.captured_at.isoformat() if row.captured_at else "",
        })
    return items


def compact_value(value: Any, max_chars: int = AI_CONTEXT_MAX_CHARS_PER_TYPE) -> Any:
    text = stable_json(value)
    if len(text) <= max_chars:
        return value
    return {
        "truncated": True,
        "max_chars": max_chars,
        "json_excerpt": text[:max_chars],
    }


def latest_match_data_for_match(session, match_id: int) -> dict[str, Any]:
    rows = session.scalars(
        select(MatchData)
        .where(MatchData.match_id == match_id)
        .order_by(MatchData.fetched_at.desc())
    ).all()
    result: dict[str, Any] = {}
    for row in rows:
        if row.data_type in result:
            continue
        result[row.data_type] = {
            "source": row.source,
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else "",
            "payload": compact_value(row.payload or {}),
        }
    return result


def match_payload(match: Match, odds: list[dict[str, Any]], match_data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "prompt_version": PROMPT_VERSION,
        "competition": match.competition_key,
        "season": match.season,
        "source_id": match.source_id,
        "stage": match.stage,
        "group": match.group_name,
        "matchday": match.matchday,
        "kickoff_time": match.kickoff_time.isoformat(),
        "home": {
            "code": match.home_team_code,
            "name": match.home_team_name,
        },
        "away": {
            "code": match.away_team_code,
            "name": match.away_team_name,
        },
        "status": match.status,
        "venue": match.venue,
        "city": match.city,
        "latest_odds": odds,
        "context_data": match_data or {},
    }


def prompt_hash_for(payload: dict[str, Any]) -> str:
    return sha256_text(stable_json({"model": MODEL, "payload": payload}))


def daily_completed_count(session) -> int:
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = session.scalars(
        select(AnalysisJob.id).where(
            AnalysisJob.status == "completed",
            AnalysisJob.finished_at >= start,
        )
    ).all()
    return len(rows)


def enqueue_analysis_jobs(limit: int | None = None) -> dict[str, Any]:
    """Create AI jobs for upcoming matches that already have odds snapshots."""
    limit = limit or AI_MAX_JOBS_PER_SYNC
    now = utcnow()
    horizon = now + timedelta(hours=AI_LOOKAHEAD_HOURS)
    queued = 0
    skipped = 0

    with session_scope() as session:
        matches = session.scalars(
            select(Match)
            .where(Match.kickoff_time >= now, Match.kickoff_time <= horizon)
            .order_by(Match.kickoff_time.asc())
            .limit(80)
        ).all()
        for match in matches:
            if queued >= limit:
                break
            odds = latest_odds_for_match(session, match.id)
            if not odds:
                skipped += 1
                continue
            payload = match_payload(match, odds, latest_match_data_for_match(session, match.id))
            prompt_hash = prompt_hash_for(payload)
            existing = session.scalar(select(AnalysisJob).where(AnalysisJob.prompt_hash == prompt_hash))
            if existing:
                skipped += 1
                continue
            priority = 10 if match.kickoff_time <= now + timedelta(hours=24) else 50
            session.add(AnalysisJob(
                match_id=match.id,
                status="queued",
                priority=priority,
                model=MODEL,
                prompt_hash=prompt_hash,
                prompt_version=PROMPT_VERSION,
                input_payload=payload,
            ))
            queued += 1

    return {"queued": queued, "skipped": skipped, "enabled": AI_ENABLED}


def system_prompt() -> str:
    return (
        "你是 AI智球 的足球赛事分析引擎。你只基于输入数据做审慎分析，"
        "输出必须是 JSON，不要输出 Markdown。不要承诺收益，不要使用绝对化措辞。"
        "重点评估比赛基本面、赔率/指数含义、风险、适合免费用户展示的简版结论、"
        "以及付费用户展示的完整解释。"
    )


def user_prompt(payload: dict[str, Any]) -> str:
    return (
        "请分析这场比赛，并返回 JSON：\n"
        "{\n"
        '  "summary": "一句话简版结论",\n'
        '  "confidence": 0.0,\n'
        '  "best_pick": {"market": "...", "selection": "...", "price": 0.0, "reason": "..."},\n'
        '  "risk": {"level": "low|medium|high", "main": "...", "avoid_if": "..."},\n'
        '  "pro": {"full_reasoning": "...", "odds_read": "...", "alternatives": [], "watchlist": []}\n'
        "}\n\n"
        f"输入数据：{stable_json(payload)}"
    )


def system_prompt() -> str:
    return (
        "你是 AI智球 的足球赛事分析引擎。只能基于输入数据做审慎分析，输出必须是 JSON，不要输出 Markdown。"
        "不要承诺收益，不要使用绝对化措辞。重点评估比赛基本面、指数含义、官方公开参考、赛况事件、技术统计和阵容信息。"
        "如果某类数据缺失，要把缺失本身写入风险判断，不能编造。"
        "同时输出适合免费用户展示的简版结论，以及付费用户展示的完整解释。"
    )


def user_prompt(payload: dict[str, Any]) -> str:
    return (
        "请分析这场比赛，并返回 JSON。输入数据中的 context_data 可能包含："
        "official_market=官方公开参考，events=赛况事件，statistics=技术统计，lineups=阵容。\n"
        "{\n"
        '  "summary": "一句话简版结论",\n'
        '  "confidence": 0.0,\n'
        '  "best_pick": {"market": "...", "selection": "...", "price": 0.0, "reason": "..."},\n'
        '  "risk": {"level": "low|medium|high", "main": "...", "avoid_if": "..."},\n'
        '  "pro": {"full_reasoning": "...", "odds_read": "...", "alternatives": [], "watchlist": []}\n'
        "}\n\n"
        f"输入数据：{stable_json(payload)}"
    )


def extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    return json.loads(raw)


def call_model(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    if not API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    resp = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": user_prompt(payload)},
            ],
            "temperature": 0.2,
        },
        timeout=60,
    )
    if not resp.ok:
        raise RuntimeError(f"AI API {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage") or {}
    return extract_json(content), {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def complete_job(job_id: int) -> dict[str, Any]:
    with session_scope() as session:
        job = session.get(AnalysisJob, job_id)
        if not job:
            return {"ok": False, "error": "job not found"}
        job.status = "running"
        job.started_at = utcnow()
        job.attempts += 1
        payload = job.input_payload
        session.flush()

    try:
        output, usage = call_model(payload)
        with session_scope() as session:
            job = session.get(AnalysisJob, job_id)
            job.status = "completed"
            job.finished_at = utcnow()
            job.output_payload = output
            job.prompt_tokens = usage["prompt_tokens"]
            job.completion_tokens = usage["completion_tokens"]
            job.total_tokens = usage["total_tokens"]
            session.add(AnalysisResult(
                match_id=job.match_id,
                status="completed",
                model=job.model,
                payload={
                    "prompt_hash": job.prompt_hash,
                    "prompt_version": job.prompt_version,
                    "visibility": {"free": ["summary", "confidence", "best_pick"], "pro": ["pro", "risk"]},
                    "analysis": output,
                    "usage": usage,
                },
            ))
        return {"ok": True, "jobId": job_id, "usage": usage}
    except Exception as exc:
        with session_scope() as session:
            job = session.get(AnalysisJob, job_id)
            if job:
                job.status = "failed" if job.attempts >= AI_MAX_RETRIES else "queued"
                job.error = str(exc)
                job.finished_at = utcnow()
        return {"ok": False, "jobId": job_id, "error": str(exc)}


def run_analysis_jobs(limit: int | None = None) -> dict[str, Any]:
    limit = limit or AI_MAX_JOBS_PER_SYNC
    if not AI_ENABLED:
        return {"enabled": False, "ran": 0, "results": []}
    if not API_KEY:
        return {"enabled": True, "ran": 0, "error": "OPENAI_API_KEY is not configured", "results": []}

    with session_scope() as session:
        completed_today = daily_completed_count(session)
        allowance = max(0, AI_DAILY_JOB_LIMIT - completed_today)
        jobs = session.scalars(
            select(AnalysisJob)
            .where(AnalysisJob.status == "queued", AnalysisJob.attempts < AI_MAX_RETRIES)
            .order_by(AnalysisJob.priority.asc(), AnalysisJob.created_at.asc())
            .limit(min(limit, allowance))
        ).all()
        job_ids = [job.id for job in jobs]

    results = [complete_job(job_id) for job_id in job_ids]
    return {
        "enabled": True,
        "dailyLimit": AI_DAILY_JOB_LIMIT,
        "remainingBeforeRun": allowance,
        "ran": len(results),
        "results": results,
    }


def analysis_job_stats() -> dict[str, int]:
    with session_scope() as session:
        rows = session.scalars(select(AnalysisJob.status)).all()
    stats: dict[str, int] = {}
    for status in rows:
        stats[status] = stats.get(status, 0) + 1
    return stats
