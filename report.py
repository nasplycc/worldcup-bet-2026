from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from state import project_path


def format_kickoff(value: str, timezone: str = "Asia/Shanghai") -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        tz = dt_timezone(timedelta(hours=8)) if timezone == "Asia/Shanghai" else dt_timezone.utc
    local = dt.astimezone(tz)
    return local.strftime("%Y-%m-%d %H:%M")


def compact_pick(rec: dict[str, Any]) -> str:
    picks = "/".join(rec.get("picks", []))
    backup = rec.get("backup", [])
    if backup:
        return f"{picks}，防 {'/'.join(backup)}"
    return picks


def render_report(
    analyses: list[dict[str, Any]],
    parlay: list[dict[str, Any]],
    mode: str,
    days: int,
    source_file: str,
    used_fallback: bool,
    timezone: str = "Asia/Shanghai",
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 2026 世界杯竞彩足球建议",
        "",
        f"- 生成时间：{now}",
        f"- 模式：{mode}",
        f"- 覆盖范围：未来 {days} 天",
        f"- 数据源：{source_file}{'（回退数据）' if used_fallback else ''}",
        f"- 说明：当前为第一阶段规则策略输出，CSV 数据可替换为真实竞彩赛程和赔率。",
        "",
    ]

    if not analyses:
        lines.extend([
            "## 暂无可推荐比赛",
            "",
            "当前筛选范围内没有比赛。可以调大 `--days`，或更新 `data/sample_matches.json`。",
        ])
        return "\n".join(lines) + "\n"

    lines.append("## 单场建议")
    lines.append("")

    for idx, item in enumerate(analyses, start=1):
        lines.append(f"### {idx}. {item['home_team']} vs {item['away_team']}")
        lines.append("")
        lines.append(f"- 开赛时间：{format_kickoff(item['kickoff_time'], timezone)}（{timezone}）")
        if item.get("venue") or item.get("city"):
            venue = " / ".join(part for part in [item.get("venue"), item.get("city")] if part)
            lines.append(f"- 场地：{venue}")
        if item.get("group"):
            lines.append(f"- 分组：{item['group']}")
        if item.get("notes"):
            lines.append(f"- 备注：{'；'.join(item['notes'])}")
        if item.get("home_profile") and item.get("away_profile"):
            hp = item["home_profile"]
            ap = item["away_profile"]
            lines.append(
                f"- 球队画像：{item['home_team']}({hp.get('tier', 'unknown')}, 攻{hp.get('attack', '-')}/防{hp.get('defense', '-')}) "
                f"vs {item['away_team']}({ap.get('tier', 'unknown')}, 攻{ap.get('attack', '-')}/防{ap.get('defense', '-')})"
            )
        if not item.get("odds_available"):
            lines.append("- 竞彩赔率：待开售")
            lines.append("- 当前建议：只展示 FIFA 官方赛程，暂不生成正式投注号码。")
        else:
            movement = item.get("odds_movement", {})
            if movement:
                lines.append(f"- 赔率追踪：{movement.get('summary', '')}")
                significant = movement.get("significant_changes", [])
                if significant:
                    formatted_changes = []
                    for change in significant[:3]:
                        formatted_changes.append(
                            f"{change.get('market')} {change.get('old')}→{change.get('new')}"
                        )
                    lines.append(f"- 显著变化：{'；'.join(formatted_changes)}")
            for rec in item["recommendations"]:
                lines.append(
                    f"- {rec['play']}：{compact_pick(rec)} | 信心 {rec['confidence']:.0%} | 风险 {rec['risk_level']} | {rec['reason']}"
                )
        if item.get("openclaw_analysis"):
            pack = item["openclaw_analysis"]
            lines.append(f"- OpenClaw分析包：{pack.get('status', '')}")
            if pack.get("instruction"):
                lines.append(f"- OpenClaw任务：{pack['instruction']}")
            if pack.get("candidate_bets"):
                formatted = []
                for pick in pack["candidate_bets"][:3]:
                    odds = pick.get("odds")
                    odds_text = f"@{float(odds):.2f}" if isinstance(odds, (int, float)) else ""
                    formatted.append(f"{pick.get('play', '')} {pick.get('pick', '')}{odds_text}")
                lines.append(f"- 候选高赔方向：{'；'.join(formatted)}")
            if pack.get("search_queries"):
                lines.append(f"- 建议搜索：{'；'.join(pack['search_queries'][:3])}")
        elif item.get("ai_value_picks"):
            formatted = []
            for pick in item["ai_value_picks"][:3]:
                odds = pick.get("odds")
                odds_text = f"@{float(odds):.2f}" if isinstance(odds, (int, float)) else ""
                formatted.append(f"{pick.get('play', '')} {pick.get('pick', '')}{odds_text}")
            lines.append(f"- 高赔备选：{'；'.join(formatted)}")
        lines.append("")

    lines.append("## 混合过关参考")
    lines.append("")
    if not parlay:
        lines.append("暂无满足过关信心阈值的组合，建议只看单场。")
    else:
        for idx, item in enumerate(parlay, start=1):
            pick = "/".join(item["pick"])
            lines.append(
                f"{idx}. {item['match']}：{item['play']} {pick} | 信心 {item['confidence']:.0%}"
            )
        lines.append("")
        lines.append("建议：过关仅作参考，临场阵容和赔率变化明显时应重新生成。")

    return "\n".join(lines) + "\n"


def report_path(reports_dir: str, mode: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return project_path(reports_dir) / f"jingcai_{mode}_{stamp}.md"
