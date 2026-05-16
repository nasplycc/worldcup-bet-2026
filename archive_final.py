from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from state import ensure_parent, load_json, project_path, save_json


def copy_if_exists(src: str, dst: Path) -> str | None:
    source = project_path(src)
    if not source.exists():
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dst)
    return str(dst)


def load_config(path: str) -> dict[str, Any]:
    return load_json(path, {})


def archive_final(config_path: str = "config.json") -> dict[str, Any]:
    config = load_config(config_path)
    data_cfg = config.get("data", {})
    archive_dir = project_path(data_cfg.get("final_archive_dir", "data/final_reports"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    md_dst = archive_dir / f"final_recommendations_{stamp}.md"
    json_dst = archive_dir / f"final_recommendations_{stamp}.json"

    md_archived = copy_if_exists(data_cfg.get("final_markdown_file", "data/final_recommendations.md"), md_dst)
    json_archived = copy_if_exists(data_cfg.get("final_json_file", "data/final_recommendations.json"), json_dst)

    if not md_archived and not json_archived:
        raise FileNotFoundError("No final recommendation files found to archive.")

    index_path = data_cfg.get("final_archive_index", "data/final_reports/index.json")
    index = load_json(index_path, [])
    entry = {
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "markdown": md_archived,
        "json": json_archived,
    }
    index.append(entry)
    save_json(index_path, index)
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive final OpenClaw recommendations.")
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    entry = archive_final(args.config)
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
