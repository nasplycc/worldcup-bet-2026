from __future__ import annotations

from dataclasses import dataclass
from math import exp
from typing import Any


SPF_LABELS = {"3": "主胜", "1": "平", "0": "主负"}
JQS_ORDER = ["0", "1", "2", "3", "4", "5", "6", "7+"]


@dataclass
class Pick:
    play: str
    picks: list[str]
    backup: list[str]
    confidence: float
    risk_level: str
    reason: str
    odds: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "play": self.play,
            "picks": self.picks,
            "backup": self.backup,
            "confidence": round(self.confidence, 3),
            "risk_level": self.risk_level,
            "reason": self.reason,
            "odds": self.odds,
        }


def normalize(values: dict[str, float]) -> dict[str, float]:
    inv = {k: 1.0 / v for k, v in values.items() if v and v > 0}
    total = sum(inv.values()) or 1.0
    return {k: v / total for k, v in inv.items()}


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def model_spf_probability(match: dict[str, Any], cfg: dict[str, Any]) -> dict[str, float]:
    home_rating = float(match.get("home_rating", 80))
    away_rating = float(match.get("away_rating", 80))
    diff = home_rating - away_rating
    if not match.get("neutral_ground", True):
        diff += cfg.get("home_advantage", 0.035) * cfg.get("rating_scale", 18.0)

    scale = float(cfg.get("rating_scale", 18.0))
    home_raw = 1.0 / (1.0 + exp(-diff / scale))
    close_bonus = max(0.0, 1.0 - abs(diff) / 14.0)
    draw = cfg.get("draw_base", 0.27) + cfg.get("draw_close_rating_bonus", 0.08) * close_bonus
    draw = clamp(draw, 0.18, 0.36)

    non_draw = 1.0 - draw
    home = home_raw * non_draw
    away = (1.0 - home_raw) * non_draw
    return {"3": home, "1": draw, "0": away}


def blend_probability(model: dict[str, float], market: dict[str, float]) -> dict[str, float]:
    keys = set(model) | set(market)
    blended = {k: model.get(k, 0) * 0.55 + market.get(k, 0) * 0.45 for k in keys}
    total = sum(blended.values()) or 1.0
    return {k: v / total for k, v in blended.items()}


def risk_level(confidence: float) -> str:
    if confidence >= 0.7:
        return "低"
    if confidence >= 0.6:
        return "中"
    return "高"


def decision_confidence(top_prob: float, second_prob: float, floor: float = 0.45) -> float:
    gap = max(0.0, top_prob - second_prob)
    return clamp(floor + top_prob * 0.34 + gap * 0.85, 0.0, 0.86)


def recommend_spf(match: dict[str, Any], cfg: dict[str, Any]) -> Pick:
    odds = match["odds"]["spf"]
    market = normalize(odds)
    model = model_spf_probability(match, cfg)
    prob = blend_probability(model, market)
    ranked = sorted(prob.items(), key=lambda item: item[1], reverse=True)
    top, second = ranked[0], ranked[1]
    picks = [top[0]]
    backup = []
    if second[1] >= cfg.get("confidence_threshold", 0.56) - 0.12:
        backup = [second[0]]
    confidence = decision_confidence(top[1], second[1])
    reason = f"{SPF_LABELS[top[0]]}概率最高；模型与赔率综合概率约 {top[1]:.0%}，决策评分 {confidence:.0%}。"
    if backup:
        reason += f" 次选防 {SPF_LABELS[backup[0]]}。"
    return Pick("胜平负", picks, backup, confidence, risk_level(confidence), reason, odds)


def handicap_result(home_goals_edge: float, handicap: int) -> str:
    adjusted = home_goals_edge + handicap
    if adjusted > 0.35:
        return "3"
    if adjusted < -0.35:
        return "0"
    return "1"


def recommend_rqspf(match: dict[str, Any], cfg: dict[str, Any]) -> Pick:
    odds_data = match["odds"]["rqspf"]
    handicap = int(odds_data.get("handicap", 0))
    odds = {k: float(v) for k, v in odds_data.items() if k in SPF_LABELS}
    market = normalize(odds)
    rating_edge = (float(match.get("home_rating", 80)) - float(match.get("away_rating", 80))) / 10.0
    if not match.get("neutral_ground", True):
        rating_edge += 0.35
    model_pick = handicap_result(rating_edge, handicap)
    model = {k: 0.2 for k in SPF_LABELS}
    model[model_pick] = 0.6
    prob = blend_probability(model, market)
    ranked = sorted(prob.items(), key=lambda item: item[1], reverse=True)
    top, second = ranked[0], ranked[1]
    backup = [second[0]] if second[1] >= 0.28 else []
    confidence = max(
        0.0,
        decision_confidence(top[1], second[1], floor=0.42)
        - cfg.get("handicap_confidence_penalty", 0.08),
    )
    reason = f"让球为主队 {handicap:+d}，综合后倾向 {SPF_LABELS[top[0]]}。"
    if backup:
        reason += f" 可防 {SPF_LABELS[backup[0]]}。"
    return Pick("让球胜平负", [top[0]], backup, confidence, risk_level(confidence), reason, odds_data)


def recommend_jqs(match: dict[str, Any], cfg: dict[str, Any]) -> Pick:
    odds = match["odds"]["jqs"]
    market = normalize({k: float(v) for k, v in odds.items() if k in JQS_ORDER})
    ranked = sorted(market.items(), key=lambda item: item[1], reverse=True)
    primary = [ranked[0][0]]
    backup = [ranked[1][0]] if len(ranked) > 1 else []
    confidence = clamp(
        0.42 + ranked[0][1] * 0.42 + (ranked[0][1] - ranked[1][1]) * 0.65,
        0.0,
        0.72,
    )
    reason = f"总进球赔率重心在 {primary[0]} 球。"
    if backup:
        reason += f" 备选 {backup[0]} 球。"
    return Pick("总进球", primary, backup, confidence, risk_level(confidence), reason, odds)
