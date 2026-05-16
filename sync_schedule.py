from __future__ import annotations

import argparse

from fifa_schedule import FIFA_MATCHES_API, fetch_worldcup_2026_schedule
from state import save_json


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 FIFA 官方 2026 世界杯赛程")
    parser.add_argument("--output", default="data/worldcup_2026_schedule.json")
    args = parser.parse_args()

    matches = fetch_worldcup_2026_schedule()
    path = save_json(args.output, matches)
    print(f"已同步 FIFA 官方赛程: {len(matches)} 场")
    print(f"输出文件: {path}")
    print(f"来源: {FIFA_MATCHES_API}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
