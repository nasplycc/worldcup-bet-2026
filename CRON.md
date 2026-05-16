# NAS / OpenClaw 定时建议

第一阶段建议由 OpenClaw 或 NAS 计划任务每天运行 1-2 次。

## 每天早上生成未来 3 天建议

```bash
cd /vol1/1000/code/worldcup-bet-2026 && ./run_openclaw.sh --mode upcoming --days 3 --ai --alerts
```

## 比赛日生成当天建议

```bash
cd /vol1/1000/code/worldcup-bet-2026 && ./run_openclaw.sh --mode today --ai --alerts
```

## 测试期生成样例报告

```bash
cd /vol1/1000/code/worldcup-bet-2026 && ./run_openclaw.sh --mode upcoming --days 45
```

## 同步 FIFA 官方赛程

```bash
cd /vol1/1000/code/worldcup-bet-2026 && python3 sync_schedule.py
```
