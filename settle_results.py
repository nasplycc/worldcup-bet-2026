from __future__ import annotations

import argparse
import json

from settlement import load_results, render_settlement_markdown, settle_final_recommendations
from state import load_json, save_json, ensure_parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Settle final Jingcai recommendations with match results.")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--final", default=None, help="Final recommendations JSON")
    parser.add_argument("--results", default=None, help="Match results CSV")
    parser.add_argument("--stake", type=float, default=10.0, help="Theoretical stake per bet")
    args = parser.parse_args()

    config = load_json(args.config, {})
    data_cfg = config.get("data", {})
    final_file = args.final or data_cfg.get("final_json_file", "data/final_recommendations.json")
    results_file = args.results or data_cfg.get("match_results_file", "data/match_results.csv")
    output_json = data_cfg.get("settlement_json_file", "data/settlement_report.json")
    output_md = data_cfg.get("settlement_markdown_file", "data/settlement_report.md")

    final_data = load_json(final_file, {})
    results = load_results(results_file)
    report = settle_final_recommendations(final_data, results, args.stake)

    save_json(output_json, report)
    md_path = ensure_parent(output_md)
    md_path.write_text(render_settlement_markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Settlement JSON: {output_json}")
    print(f"Settlement MD: {output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
