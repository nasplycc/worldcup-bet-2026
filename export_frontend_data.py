from __future__ import annotations

import argparse

from frontend_data import merge_worldcup_frontend, recommendations_to_frontend, schedule_to_frontend
from state import load_json, save_json


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 GitHub Pages 可读取的世界杯前端数据")
    parser.add_argument("--input", default="data/recommendations.json")
    parser.add_argument("--output", default="docs/data/worldcup_matches.json")
    args = parser.parse_args()

    payload = load_json(args.input, {})
    schedule_payload = load_json("data/worldcup_2026_schedule.json", [])
    schedule_frontend = schedule_to_frontend(schedule_payload) if schedule_payload else {}
    if payload:
        recommendations_frontend = recommendations_to_frontend(payload)
        frontend_payload = (
            merge_worldcup_frontend(schedule_frontend, recommendations_frontend)
            if schedule_frontend
            else recommendations_frontend
        )
    else:
        frontend_payload = schedule_frontend
    path = save_json(args.output, frontend_payload)
    print(f"已导出前端数据: {path}，共 {frontend_payload['count']} 场")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
