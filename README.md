# WorldCup Bet 2026 Jingcai Assistant

给 NAS/OpenClaw 调用的 2026 世界杯竞彩足球数据整理项目。

架构：

```text
Python：同步赛程、读取赔率、生成候选投注方向、输出 JSON
OpenClaw：读取 JSON，使用自身模型和联网能力做最终 AI 分析
```

Python 内部不需要配置模型 key。

## Data

优先读取：

```text
data/jingcai_matches.csv
```

如果不存在，回退到 FIFA 官方 104 场赛程：

```text
data/worldcup_2026_schedule.json
```

同步 FIFA 官方赛程：

```bash
python sync_schedule.py
```

校验数据：

```bash
python main.py --validate-data
```

## OpenClaw Flow

运行：

```bash
./run_openclaw.sh --mode upcoming --days 45 --ai --json
```

Python 输出：

```text
data/recommendations.json
data/reports/jingcai_*.md
```

OpenClaw 读取 `data/recommendations.json` 后，必须直接生成并保存：

```text
data/final_recommendations.md
data/final_recommendations.json
```

保存后归档：

```bash
python archive_final.py
```

归档位置：

```text
data/final_reports/
data/final_reports/index.json
```

`openclaw_analysis` 会包含：

```text
candidate_bets          候选高赔投注方向
team_profiles           主客队基础画像
search_queries          建议 OpenClaw 联网检索的关键词
candidate_pool_policy   候选池刷新条件和调整规则
odds_movement           相对上次运行的赔率变化
```

候选池需要随赛前信息动态调整。赔率变化、首发确认、伤停、停赛、小组形势、天气场地和临场轮换都会改变推荐优先级；OpenClaw 最终分析时必须按最新信息重新排序或剔除候选。

每次运行会更新：

```text
data/odds_snapshot.json
data/odds_movements.json
```

`odds_snapshot.json` 是当前赔率快照；`odds_movements.json` 只记录显著变化，方便 OpenClaw 判断升赔搏冷、降赔避险、让球盘或进球数方向是否需要重排。

最终 JSON 结构参考：

```text
data/final_recommendations.schema.json
```

## Commands

```bash
./run_openclaw.sh --mode today --ai --json
./run_openclaw.sh --mode upcoming --days 3 --ai --json
./run_openclaw.sh --mode upcoming --matches data/jingcai_matches.csv --ai --json
```

## Settlement

比赛结束后，复制模板并录入赛果：

```bash
cp data/match_results.example.csv data/match_results.csv
```

复盘最终推荐：

```bash
python settle_results.py --stake 10
```

输出：

```text
data/settlement_report.md
data/settlement_report.json
```

## Codes

```text
胜平负：3 主胜，1 平，0 主负
让球胜平负：3 让胜，1 让平，0 让负
总进球：0 / 1 / 2 / 3 / 4 / 5 / 6 / 7+
```
