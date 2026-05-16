from __future__ import annotations

from typing import Any


def pick_odds(rec: dict[str, Any], pick: str) -> float:
    odds = rec.get("odds", {})
    value = odds.get(pick)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def clamp(value: float, floor: float, ceiling: float) -> float:
    return max(floor, min(ceiling, value))


def profile_number(profile: dict[str, Any], field: str, default: float = 50.0) -> float:
    value = profile.get(field, default)
    if isinstance(value, (int, float)):
        return float(value)
    return default


def tier_value(tier: str) -> float:
    order = {
        "elite": 5,
        "strong": 4,
        "solid": 3,
        "mid": 2,
        "underdog": 1,
        "unknown": 2.5,
    }
    return float(order.get(str(tier).lower(), 2.5))


def team_values(item: dict[str, Any]) -> dict[str, float]:
    home = item.get("home_profile", {})
    away = item.get("away_profile", {})
    return {
        "attack_edge": profile_number(home, "attack") - profile_number(away, "attack"),
        "defense_edge": profile_number(home, "defense") - profile_number(away, "defense"),
        "depth_edge": profile_number(home, "squad_depth") - profile_number(away, "squad_depth"),
        "experience_edge": profile_number(home, "tournament_experience") - profile_number(away, "tournament_experience"),
        "home_upset": profile_number(home, "upset_potential"),
        "away_upset": profile_number(away, "upset_potential"),
        "home_volatility": profile_number(home, "volatility"),
        "away_volatility": profile_number(away, "volatility"),
        "tier_edge": tier_value(home.get("tier", "unknown")) - tier_value(away.get("tier", "unknown")),
    }


def score_payout(odds: float, style: str) -> float:
    if style == "aggressive":
        target = clamp((odds - 1.55) / 3.45, 0.0, 1.0)
        return round(18 + target * 28, 3)
    target = clamp((odds - 1.35) / 2.65, 0.0, 1.0)
    return round(10 + target * 22, 3)


def score_confidence(confidence: float) -> float:
    return round(clamp(confidence, 0.0, 1.0) * 24, 3)


def matchup_fit(play: str, pick: str, item: dict[str, Any]) -> float:
    values = team_values(item)
    attack_edge = values["attack_edge"]
    defense_edge = values["defense_edge"]
    strength_edge = (attack_edge + defense_edge + values["depth_edge"] + values["experience_edge"]) / 4
    volatility = (values["home_volatility"] + values["away_volatility"]) / 2

    if play == "胜平负":
        if pick == "3":
            return round(12 + clamp(strength_edge / 2.4, -8, 10), 3)
        if pick == "0":
            return round(12 + clamp(-strength_edge / 2.4, -8, 10), 3)
        if pick == "1":
            close_bonus = 12 - clamp(abs(strength_edge) / 2.2, 0, 10)
            return round(close_bonus + clamp(volatility / 18, 0, 4), 3)

    if play == "让球胜平负":
        if pick == "1":
            fit = 14 - abs(strength_edge - 8) / 2.2
            return round(clamp(fit, 2, 16), 3)
        if pick == "3":
            return round(8 + clamp((strength_edge - 10) / 2.0, -4, 10), 3)
        if pick == "0":
            return round(8 + clamp((-strength_edge + values["away_upset"] - 55) / 3.0, -4, 12), 3)

    if play == "总进球":
        total_attack = profile_number(item.get("home_profile", {}), "attack") + profile_number(item.get("away_profile", {}), "attack")
        weak_defense = 200 - profile_number(item.get("home_profile", {}), "defense") - profile_number(item.get("away_profile", {}), "defense")
        tempo_score = (total_attack + weak_defense + volatility) / 3
        try:
            goals = 7 if pick == "7+" else int(pick)
        except ValueError:
            goals = 3
        if goals in (2, 3):
            return round(10 + clamp((tempo_score - 55) / 4, -5, 8), 3)
        if goals in (4, 5):
            return round(8 + clamp((tempo_score - 62) / 3, -5, 10), 3)
        if goals <= 1:
            return round(8 + clamp((58 - tempo_score) / 3, -5, 8), 3)
        return round(5 + clamp((tempo_score - 70) / 2, -4, 8), 3)

    return 6.0


def upset_value(play: str, pick: str, odds: float, item: dict[str, Any]) -> float:
    values = team_values(item)
    strength_edge = (values["attack_edge"] + values["defense_edge"] + values["tier_edge"] * 8) / 3
    high_odds_bonus = clamp((odds - 2.6) * 4, 0, 11)
    volatility_bonus = clamp((values["home_volatility"] + values["away_volatility"] - 105) / 8, 0, 6)

    if play == "胜平负" and pick == "0":
        return round(high_odds_bonus + volatility_bonus + clamp((values["away_upset"] - 55 - strength_edge) / 6, 0, 8), 3)
    if play == "胜平负" and pick == "1":
        return round(high_odds_bonus * 0.8 + volatility_bonus + clamp((10 - abs(strength_edge)) / 3, 0, 5), 3)
    if play == "让球胜平负" and pick in {"0", "1"}:
        return round(high_odds_bonus + volatility_bonus + clamp((values["away_upset"] - 50) / 10, 0, 5), 3)
    if play == "总进球" and odds >= 3:
        return round(high_odds_bonus + volatility_bonus, 3)
    return round(high_odds_bonus * 0.35, 3)


def risk_penalty(play: str, pick: str, odds: float, confidence: float, item: dict[str, Any]) -> float:
    values = team_values(item)
    penalty = 0.0
    if odds >= 4.8:
        penalty += (odds - 4.8) * 3
    if confidence < 0.52:
        penalty += (0.52 - confidence) * 28
    if item.get("home_profile", {}).get("tier") == "unknown" or item.get("away_profile", {}).get("tier") == "unknown":
        penalty += 3
    if play == "总进球" and pick in {"6", "7+"}:
        penalty += 4
    if play == "胜平负" and pick == "3" and values["away_upset"] >= 68:
        penalty += 2.5
    return round(penalty, 3)


def strategy_tags(play: str, pick: str, odds: float, item: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    if odds >= 3.0:
        tags.append("高赔")
    if odds >= 4.2:
        tags.append("深水搏冷")
    if play == "让球胜平负" and pick == "1":
        tags.append("让球平")
        tags.append("强队小胜")
    elif play == "让球胜平负" and pick == "0":
        tags.append("受让方向")
    elif play == "总进球":
        tags.append("进球数")
        if pick in {"3", "4", "5"}:
            tags.append("中高进球")
    elif play == "胜平负" and pick == "1":
        tags.append("平局冷门")
    elif play == "胜平负" and pick == "0":
        tags.append("客胜冷门")

    home_tags = item.get("home_profile", {}).get("tags", [])
    away_tags = item.get("away_profile", {}).get("tags", [])
    for tag in list(home_tags)[:2] + list(away_tags)[:2]:
        if tag not in tags:
            tags.append(str(tag))
    return tags[:6]


def score_candidate(rec: dict[str, Any], pick: str, odds: float, item: dict[str, Any], style: str) -> dict[str, Any]:
    confidence = float(rec.get("confidence", 0))
    play = rec.get("play", "")
    payout = score_payout(odds, style)
    confidence_score = score_confidence(confidence)
    fit = matchup_fit(play, pick, item)
    upset = upset_value(play, pick, odds, item)
    penalty = risk_penalty(play, pick, odds, confidence, item)
    total = payout + confidence_score + fit + upset - penalty
    return {
        "candidate_score": round(total, 3),
        "score_breakdown": {
            "payout": payout,
            "confidence": confidence_score,
            "matchup_fit": fit,
            "upset_value": upset,
            "risk_penalty": penalty,
        },
    }


def local_value_candidates(item: dict[str, Any], style: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for rec in item.get("recommendations", []):
        for pick in list(rec.get("picks", [])) + list(rec.get("backup", [])):
            odds = pick_odds(rec, pick)
            if odds <= 0:
                continue
            scoring = score_candidate(rec, pick, odds, item, style)
            confidence = float(rec.get("confidence", 0))
            candidates.append(
                {
                    "play": rec.get("play", ""),
                    "pick": pick,
                    "odds": odds,
                    "candidate_score": scoring["candidate_score"],
                    "score_breakdown": scoring["score_breakdown"],
                    "strategy_tags": strategy_tags(rec.get("play", ""), pick, odds, item),
                    "rule_confidence": confidence,
                    "rule_reason": rec.get("reason", ""),
                    "risk_hint": "高赔率方向波动大，适合小注搏回报；若赛前伤停、首发或赔率明显反向变化，应重新筛选候选池。",
                }
            )
    candidates.sort(key=lambda row: row["candidate_score"], reverse=True)
    return candidates


def search_queries(item: dict[str, Any]) -> list[str]:
    home = item.get("home_team", "")
    away = item.get("away_team", "")
    base = f"{home} vs {away}"
    queries = [
        f"{base} World Cup 2026 preview",
        f"{home} injury news World Cup 2026",
        f"{away} injury news World Cup 2026",
        f"{home} predicted lineup World Cup 2026",
        f"{away} predicted lineup World Cup 2026",
        f"{base} team news",
        f"{base} tactical preview",
    ]
    group = item.get("group")
    if group:
        queries.append(f"World Cup 2026 {group} qualification scenarios {home} {away}")
    return queries


def team_edge_summary(item: dict[str, Any]) -> dict[str, Any]:
    home = item.get("home_profile", {})
    away = item.get("away_profile", {})
    fields = ["attack", "defense", "tournament_experience", "squad_depth", "upset_potential", "volatility"]
    edge = {}
    for field in fields:
        h = home.get(field)
        a = away.get(field)
        if isinstance(h, (int, float)) and isinstance(a, (int, float)):
            edge[field] = round(h - a, 1)
    return {
        "home_tier": home.get("tier", "unknown"),
        "away_tier": away.get("tier", "unknown"),
        "edge_home_minus_away": edge,
        "home_tags": home.get("tags", []),
        "away_tags": away.get("tags", []),
    }


def candidate_pool_policy() -> dict[str, Any]:
    return {
        "refresh_required": True,
        "refresh_triggers": [
            "竞彩官方赔率或让球数变化",
            "赛前首发阵容确认",
            "关键球员伤停、停赛或临场缺阵",
            "小组积分和出线形势变化",
            "天气、场地、旅行距离出现明显不利信息",
            "距离开赛 24 小时、6 小时、1 小时的例行刷新",
        ],
        "adjustment_rules": [
            "赔率下调但基本面不变：保留候选，但降低高赔价值标签权重",
            "赔率上调且基本面支持：提高冷门和让球方向优先级",
            "强队主力轮换或提前出线：降低胜平负正路，提升让球平、受让或进球数候选",
            "弱队防线伤停扩大：提高总进球 3/4/5 候选权重",
            "临场首发与预期相反：必须重新运行本地脚本并让 OpenClaw 复评",
        ],
    }


def build_openclaw_analysis_pack(item: dict[str, Any], style: str) -> dict[str, Any]:
    shared = {
        "team_profiles": {
            "home": item.get("home_profile", {}),
            "away": item.get("away_profile", {}),
            "edge_summary": team_edge_summary(item),
        },
        "search_queries": search_queries(item),
        "candidate_pool_policy": candidate_pool_policy(),
        "odds_movement": item.get("odds_movement", {}),
    }
    if not item.get("odds_available"):
        return {
            "status": "odds_pending",
            "instruction": "竞彩赔率待开售；OpenClaw 可先关注赛程、分组、新闻和阵容，但不要输出正式投注号码。",
            "candidate_bets": [],
            **shared,
        }

    candidates = local_value_candidates(item, style)
    return {
        "status": "ready_for_openclaw_analysis",
        "instruction": (
            "请 OpenClaw 使用自身模型和联网能力，结合球队实力、伤停、阵容、赛程、新闻、"
            "赔率回报、让球盘和冷门概率，评估这些候选投注。目标是寻找高风险高收益的价值机会，"
            "不是保守稳胆。候选池不是静态结论，若临场信息触发刷新条件，必须重新排序或剔除候选。"
        ),
        "risk_style": style,
        "candidate_bets": candidates[:6],
        **shared,
        "requested_output": {
            "best_bet": "最佳玩法、号码、赔率、下注话术",
            "value_bets": "2-3 个可搏高赔备选",
            "risk": "主要风险和放弃条件",
            "stake_style": "小注/搏冷小注/放弃",
        },
    }


def enrich_with_openclaw_pack(
    analyses: list[dict[str, Any]],
    enabled: bool,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not enabled:
        return analyses
    config = config or {}
    style = config.get("risk", {}).get("style", "aggressive")
    for item in analyses:
        item["openclaw_analysis"] = build_openclaw_analysis_pack(item, style)
    return analyses
