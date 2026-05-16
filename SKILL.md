# WorldCup Bet 2026 Jingcai Assistant

Python 只负责整理赛程、赔率和候选投注方向；最终 AI 判断由 OpenClaw 自身模型完成。

## Run

先运行：

```bash
cd /path/to/worldcup-bet-2026 && ./run_openclaw.sh --mode upcoming --days 45 --ai --json
```

然后读取：

```text
data/recommendations.json
```

如果 `recommendations[*].openclaw_analysis.status` 是 `ready_for_openclaw_analysis`，不要询问用户是否继续，必须直接完成最终分析并落盘。

## Required Output Files

OpenClaw 最终分析必须保存两个文件：

```text
data/final_recommendations.md
data/final_recommendations.json
```

保存完成后，必须调用归档脚本：

```bash
python archive_final.py
```

归档文件会写入：

```text
data/final_reports/final_recommendations_YYYYMMDD_HHMMSS.md
data/final_reports/final_recommendations_YYYYMMDD_HHMMSS.json
data/final_reports/index.json
```

`data/final_recommendations.json` 结构参考：

```text
data/final_recommendations.schema.json
```

## Analysis Task

读取每场比赛的：

```text
home_team
away_team
kickoff_time
recommendations
  openclaw_analysis.candidate_bets
  openclaw_analysis.team_profiles
  openclaw_analysis.search_queries
  openclaw_analysis.candidate_pool_policy
  openclaw_analysis.odds_movement
```

结合联网搜索和自身模型分析：

- 球队实力、排名、近期状态
- 伤病、停赛、阵容、主教练战术
- 小组形势、赛程密度、轮换动机
- 比赛地点、气候、旅行距离
- 竞彩赔率回报、让球盘、总进球赔率
- 冷门可能性和高赔率价值

优先使用 `openclaw_analysis.search_queries` 作为联网检索关键词；用 `team_profiles` 作为初始画像，但如果联网结果更新，应以最新信息为准。

## Dynamic Candidate Pool

`candidate_bets` 是赛前候选池，不是最终静态结论。OpenClaw 必须结合 `candidate_pool_policy.refresh_triggers` 判断是否需要刷新：

- 竞彩赔率或让球数变化
- 首发阵容确认
- 关键球员伤停、停赛或临场缺阵
- 小组积分、出线形势、轮换动机变化
- 天气、场地、旅行距离出现明显不利信息
- 距离开赛 24 小时、6 小时、1 小时的例行检查

如果触发刷新条件，必须重新评估 `candidate_bets` 的排序，必要时剔除原候选，不要机械沿用旧推荐。

如果存在 `openclaw_analysis.odds_movement.significant_changes`：

- 升赔：检查是否是市场低估、伤停利空、热度退潮或冷门机会
- 降赔：检查是否是信息面确认、市场追捧或赔率价值被压缩
- 让球相关赔率剧烈变化：优先复核让球胜平负候选
- 总进球赔率剧烈变化：优先复核伤停、天气、首发前锋/中卫/门将信息

目标风格：

```text
高风险高收益，优先找赔率价值和可搏冷门，不做保守稳胆。
```

## Markdown Format

每场比赛输出：

```text
## 比赛：主队 vs 客队

最佳下注：
玩法：
号码：
赔率：
线下投注话术：
为什么值得搏：
主要风险：
放弃条件：
建议注码：小注 / 搏冷小注 / 放弃
高赔备选：
```

## JSON Fields

每场比赛至少包含：

```json
{
  "match": "主队 vs 客队",
  "best_bet": {
    "play": "玩法",
    "pick": "号码",
    "odds": 0,
    "offline_phrase": "线下投注话术",
    "why": "为什么值得搏",
    "risk": "主要风险",
    "drop_conditions": "放弃条件",
    "stake_style": "小注/搏冷小注/放弃"
  },
  "value_bets": []
}
```

## Jingcai Codes

```text
胜平负：3 主胜，1 平，0 主负
让球胜平负：3 让胜，1 让平，0 让负
总进球：0 / 1 / 2 / 3 / 4 / 5 / 6 / 7+
```

## No Odds Rule

如果只有 FIFA 官方赛程，没有竞彩赔率：

```text
只分析赛程和信息面，不输出正式投注号码，不写 best_bet。
```

## Settlement

比赛结束后，用户可以录入：

```text
data/match_results.csv
```

然后运行：

```bash
python settle_results.py --stake 10
```

输出复盘：

```text
data/settlement_report.md
data/settlement_report.json
```
