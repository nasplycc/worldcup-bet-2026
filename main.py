from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

from ai_analysis import enrich_with_openclaw_pack
from alerts import send_telegram
from odds_tracking import attach_odds_movements
from report import render_report, report_path
from schedule import filter_matches, load_matches
from state import load_json, save_json
from strategy import analyze_match, build_parlay
from odds_sources import validate_matches
from teams import load_teams


PLAY_ALIASES = {
    "spf": "spf",
    "rqspf": "rqspf",
    "jqs": "jqs",
    "win_draw_loss": "spf",
    "handicap": "rqspf",
    "total_goals": "jqs",
}


def parse_plays(raw: str, config: dict) -> set[str]:
    if raw == "all":
        enabled = config.get("plays", {})
        return {play for play in ["spf", "rqspf", "jqs"] if enabled.get(play, True)}
    return {PLAY_ALIASES[p.strip()] for p in raw.split(",") if p.strip() in PLAY_ALIASES}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="2026 世界杯竞彩足球选号助手")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--mode", choices=["today", "upcoming", "parlay", "all"], default="upcoming")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--plays", default="all", help="all 或 spf,rqspf,jqs")
    parser.add_argument("--matches", default=None, help="覆盖配置中的赛程/赔率文件，支持 CSV 或 JSON")
    parser.add_argument("--validate-data", action="store_true", help="只校验赛程/赔率数据并退出")
    parser.add_argument("--ai", action="store_true", help="生成 OpenClaw AI 分析包，不在脚本内调用模型")
    parser.add_argument("--alerts", action="store_true", help="启用 Telegram 推送")
    parser.add_argument("--json", action="store_true", help="标准输出 JSON，而不是 Markdown")
    args = parser.parse_args()

    if load_dotenv:
        load_dotenv()

    config = load_json(args.config, {})
    days = args.days or config.get("strategy", {}).get("default_lookahead_days", 45)
    plays = parse_plays(args.plays, config)

    if args.alerts:
        config.setdefault("alerts", {})["telegram_enabled"] = True
    if args.ai:
        config.setdefault("features", {})["ai_analysis"] = True

    data_cfg = config.get("data", {})
    matches_file = args.matches or data_cfg.get("matches_file", "data/jingcai_matches.csv")
    fallback_file = data_cfg.get("fallback_matches_file", "data/sample_matches.json")
    matches, source_file, used_fallback = load_matches(matches_file, fallback_file)
    validation_errors = validate_matches(matches)
    if args.validate_data:
        if validation_errors:
            print("数据校验失败:")
            for error in validation_errors:
                print(f"- {error}")
            return 1
        print(f"数据校验通过: {source_file}，共 {len(matches)} 场")
        return 0
    if validation_errors:
        raise SystemExit("数据校验失败:\n" + "\n".join(f"- {error}" for error in validation_errors))

    selected = filter_matches(matches, args.mode, days)
    max_matches = config.get("strategy", {}).get("max_matches_per_report", 12)
    selected = selected[:max_matches]

    teams_file = data_cfg.get("teams_file", "data/teams.json")
    teams = load_teams(teams_file)
    analyses = [analyze_match(match, config, plays, teams) for match in selected]
    odds_movement_report = attach_odds_movements(analyses, config)
    analyses = enrich_with_openclaw_pack(analyses, config.get("features", {}).get("ai_analysis", False), config)
    parlay = build_parlay(analyses, config) if config.get("plays", {}).get("parlay", True) else []

    output = {
        "mode": args.mode,
        "days": days,
        "plays": sorted(plays),
        "matches_analyzed": len(analyses),
        "source_file": source_file,
        "used_fallback_source": used_fallback,
        "odds_movement": odds_movement_report,
        "recommendations": analyses,
        "parlay": parlay,
    }

    rec_file = config.get("data", {}).get("recommendations_file", "data/recommendations.json")
    save_json(rec_file, output)

    markdown = render_report(
        analyses,
        parlay,
        args.mode,
        days,
        source_file,
        used_fallback,
        config.get("timezone", "Asia/Shanghai"),
    )
    path = report_path(config.get("data", {}).get("reports_dir", "data/reports"), args.mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")

    send_telegram(markdown, config)

    if args.json:
        print(json.dumps({**output, "report_file": str(path)}, ensure_ascii=False, indent=2))
    else:
        print(markdown)
        print(f"[OK] 报告已保存: {path}")
        print(f"[OK] JSON 已保存: {Path(rec_file).resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
