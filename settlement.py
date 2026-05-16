from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from state import project_path


@dataclass
class MatchResult:
    match: str
    home_score: int
    away_score: int
    handicap: int | None = None
    settled_at: str = ""
    notes: str = ""

    @property
    def total_goals(self) -> int:
        return self.home_score + self.away_score

    @property
    def spf(self) -> str:
        if self.home_score > self.away_score:
            return "3"
        if self.home_score == self.away_score:
            return "1"
        return "0"

    @property
    def rqspf(self) -> str | None:
        if self.handicap is None:
            return None
        adjusted = self.home_score + self.handicap
        if adjusted > self.away_score:
            return "3"
        if adjusted == self.away_score:
            return "1"
        return "0"

    @property
    def jqs(self) -> str:
        return "7+" if self.total_goals >= 7 else str(self.total_goals)


def parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def load_results(path: str | Path) -> dict[str, MatchResult]:
    p = project_path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    results = {}
    for row in rows:
        match = (row.get("match") or "").strip()
        if not match:
            continue
        home_score = parse_int(row.get("home_score"))
        away_score = parse_int(row.get("away_score"))
        if home_score is None or away_score is None:
            continue
        results[match] = MatchResult(
            match=match,
            home_score=home_score,
            away_score=away_score,
            handicap=parse_int(row.get("handicap")),
            settled_at=row.get("settled_at", ""),
            notes=row.get("notes", ""),
        )
    return results


def result_for_play(result: MatchResult, play: str) -> str | None:
    if play == "胜平负":
        return result.spf
    if play == "让球胜平负":
        return result.rqspf
    if play == "总进球":
        return result.jqs
    return None


def settle_bet(bet: dict[str, Any], result: MatchResult, stake: float) -> dict[str, Any]:
    play = bet.get("play", "")
    pick = str(bet.get("pick", ""))
    odds = float(bet.get("odds") or 0)
    actual = result_for_play(result, play)
    hit = actual == pick
    payout = stake * odds if hit else 0.0
    profit = payout - stake
    return {
        "play": play,
        "pick": pick,
        "odds": odds,
        "actual": actual,
        "hit": hit,
        "stake": round(stake, 2),
        "payout": round(payout, 2),
        "profit": round(profit, 2),
    }


def iter_final_items(final_data: dict[str, Any]) -> list[dict[str, Any]]:
    items = final_data.get("items")
    if isinstance(items, list):
        return items
    recommendations = final_data.get("recommendations")
    if isinstance(recommendations, list):
        return recommendations
    return []


def settle_final_recommendations(
    final_data: dict[str, Any],
    results: dict[str, MatchResult],
    stake: float,
) -> dict[str, Any]:
    settled = []
    total_stake = 0.0
    total_payout = 0.0

    for item in iter_final_items(final_data):
        match = item.get("match") or f"{item.get('home_team', '')} vs {item.get('away_team', '')}".strip()
        result = results.get(match)
        if not result:
            settled.append({"match": match, "status": "missing_result"})
            continue

        bets = []
        best_bet = item.get("best_bet")
        if isinstance(best_bet, dict) and best_bet.get("play"):
            bets.append({"kind": "best_bet", **best_bet})
        for bet in item.get("value_bets", []) or []:
            if isinstance(bet, dict) and bet.get("play"):
                bets.append({"kind": "value_bet", **bet})

        settled_bets = []
        for bet in bets:
            row = settle_bet(bet, result, stake)
            row["kind"] = bet.get("kind", "")
            settled_bets.append(row)
            total_stake += row["stake"]
            total_payout += row["payout"]

        settled.append(
            {
                "match": match,
                "score": f"{result.home_score}-{result.away_score}",
                "spf": result.spf,
                "rqspf": result.rqspf,
                "jqs": result.jqs,
                "bets": settled_bets,
            }
        )

    profit = total_payout - total_stake
    roi = profit / total_stake if total_stake else 0.0
    return {
        "summary": {
            "total_stake": round(total_stake, 2),
            "total_payout": round(total_payout, 2),
            "profit": round(profit, 2),
            "roi": round(roi, 4),
        },
        "items": settled,
    }


def render_settlement_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# 竞彩足球推荐复盘",
        "",
        f"- 总投入：{summary['total_stake']}",
        f"- 总返还：{summary['total_payout']}",
        f"- 盈亏：{summary['profit']}",
        f"- ROI：{summary['roi']:.2%}",
        "",
        "## 明细",
        "",
    ]
    for item in report["items"]:
        lines.append(f"### {item.get('match', '')}")
        if item.get("status") == "missing_result":
            lines.append("- 状态：缺少赛果")
            lines.append("")
            continue
        lines.append(f"- 比分：{item['score']} | 胜平负：{item['spf']} | 让球：{item.get('rqspf')} | 总进球：{item['jqs']}")
        for bet in item.get("bets", []):
            mark = "命中" if bet["hit"] else "未中"
            lines.append(
                f"- {bet['kind']} {bet['play']} {bet['pick']} @ {bet['odds']:.2f}：{mark}，盈亏 {bet['profit']}"
            )
        lines.append("")
    return "\n".join(lines)
