# ⚽ WorldCup Bet 2026 — AI 世界杯赛事预测引擎

> 基于 Python 数据管道 + AI 大模型推理的 2026 年世界杯全自动赛事预测系统。

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![FIFA World Cup 2026](https://img.shields.io/badge/FIFA%20World%20Cup-2026-132257?logo=fifa&logoColor=white)](https://www.fifa.com/)
[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-在线演示-181717?logo=githubpages&logoColor=white)](https://nasplycc.github.io/worldcup-bet-2026/)

> 🇬🇧 [English README](README.en.md)

---

## 🚀 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/nasplycc/worldcup-bet-2026.git
cd worldcup-bet-2026

# 2. 安装依赖
pip install -r requirements.txt

# 3. 校验数据
python main.py --validate-data

# 4. 运行分析
./run_openclaw.sh --mode upcoming --days 45 --ai --json
```

## 📖 目录

- [项目概述](#项目概述)
- [系统架构](#系统架构)
- [核心功能](#核心功能)
- [项目结构](#项目结构)
- [使用说明](#使用说明)
- [AI 分析任务包](#ai-分析任务包)
- [动态刷新机制](#动态刷新机制)
- [赛后复盘](#赛后复盘)
- [Web 前端](#web-前端)
- [部署指南](#部署指南)
- [许可证](#许可证)

---

## 项目概述

**WorldCup Bet 2026** 是一个面向 2026 年 FIFA 世界杯的双层 AI 预测系统：

| 层级 | 职责 |
|------|------|
| **Python** | 数据管道 — 同步赛程、采集市场指数、构建 10 维球队画像、生成候选分析方向 |
| **AI 大模型** | 决策引擎 — 联网搜索最新伤停/阵容/战术信息，结合数据模型做最终预测分析 |

核心设计理念：**Python 脚本中不配置任何 AI 模型的 API Key**。Python 只负责准备结构化数据，交给 AI 智能体完成推理判断。

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                   数据层（Python）                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ FIFA     │ │ 市场     │ │ 球队     │ │ 指数       │ │
│  │ 赛程     │ │ 指数     │ │ 画像库   │ │ 变动记录   │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘ │
└───────┼────────────┼────────────┼──────────────┼────────┘
        │            │            │              │
        ▼            ▼            ▼              ▼
┌─────────────────────────────────────────────────────────┐
│                  引擎层（Python）                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ 规则     │ │ 特征     │ │ 候选池   │ │ 动态       │ │
│  │ 引擎     │ │ 提取     │ │ 生成     │ │ 刷新策略   │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘ │
└───────┼────────────┼────────────┼──────────────┼────────┘
        │            │            │              │
        ▼            ▼            ▼              ▼
┌─────────────────────────────────────────────────────────┐
│                    AI 层（大模型）                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │  联网搜索 → 伤停 / 阵容 / 战术 / 天气 / 新闻      │   │
│  │  多维度交叉验证                                    │   │
│  │  输出结构化预测报告（Markdown + JSON）              │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│                    输出层                                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ 分析报告 │ │ Web 前端 │ │ Telegram │ │ 赛后       │ │
│  │ (MD/JSON)│ │ (Pages)  │ │ 推送     │ │ 复盘       │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘ │
└─────────────────────────────────────────────────────────┘
```

## 核心功能

- **104 场赛事覆盖** — FIFA 官方完整赛程同步
- **48 支球队画像** — 10 维能力模型（攻击、防守、大赛经验、阵容深度、冷门潜力、波动性等）
- **实时指数追踪** — 自动检测市场指数显著波动
- **AI 深度推理** — 大模型联网搜索最新伤停、阵容、战术、天气信息
- **动态候选刷新** — 临场信息变化时自动重新评估预测权重
- **赛后自动复盘** — 赛果录入 → 自动结算准确率 → 生成复盘报告
- **Telegram 实时推送** — 重要赛事数据变化即时通知
- **Web 前端页面** — SaaS 风格 Landing Page，托管于 GitHub Pages

## 项目结构

```
worldcup-bet-2026/
├── main.py                  # 入口 — 编排数据 + AI 分析流程
├── config.json              # 配置文件（玩法、策略、风险、推送）
├── requirements.txt         # Python 依赖
├── run_openclaw.sh          # OpenClaw 流程 Shell 封装
│
├── ai_analysis.py           # 构建 AI 分析任务包
├── strategy.py              # 比赛分析 + 混合过关组合
├── jingcai.py               # 竞彩推荐引擎（胜平负/让球/总进球）
├── odds_tracking.py         # 指数变动检测与阈值告警
├── odds_sources.py          # 市场数据校验
├── teams.py                 # 球队画像构建器（10 维模型）
├── schedule.py              # 比赛筛选与加载
├── sync_schedule.py         # FIFA 官方赛程同步
├── fifa_schedule.py         # FIFA 数据源处理
├── report.py                # Markdown 报告渲染
├── settlement.py            # 赛后结算引擎
├── settle_results.py        # 赛果复盘 CLI
├── archive_final.py         # 报告归档 + 索引管理
├── alerts.py                # Telegram 消息推送
├── state.py                 # JSON 状态持久化
├── SKILL.md                 # AI 技能描述文件（自然语言指令）
├── CRON.md                  # 定时任务文档
│
├── data/                    # 数据目录
│   ├── jingcai_matches.csv  # 竞彩官方赛程与指数
│   ├── worldcup_2026_schedule.json  # FIFA 104 场备用赛程
│   ├── teams.json           # 48 支球队 10 维画像数据
│   ├── recommendations.json # 最新分析输出
│   ├── reports/             # 生成的 Markdown 报告
│   ├── final_reports/       # 归档报告 + 索引
│   ├── odds_snapshot.json   # 当前指数快照
│   ├── odds_movements.json  # 显著指数变动记录
│   └── final_recommendations.schema.json  # 输出结构定义
│
└── docs/                    # GitHub Pages 前端
    ├── index.html           # Landing Page（黑夜/白天模式）
    ├── architecture.html    # 系统架构图
    ├── ai-analysis-pack.html # AI 分析任务包数据预览
    ├── ai-demo.html         # AI 聊天演示界面
    ├── skill-and-log.html   # 技能文件 + 终端日志预览
    ├── report-preview.html  # 分析报告示例
    ├── settlement-report.html # 赛后复盘报告预览
    └── theme-compare.html   # 主题切换效果对比
```

## 使用说明

### 基本命令

```bash
# 校验数据完整性
python main.py --validate-data

# 分析今日比赛
./run_openclaw.sh --mode today --ai --json

# 分析未来 3 天比赛
./run_openclaw.sh --mode upcoming --days 3 --ai --json

# 分析指定 CSV 赛程
./run_openclaw.sh --mode upcoming --matches data/jingcai_matches.csv --ai --json

# 启用 Telegram 推送
./run_openclaw.sh --mode upcoming --days 3 --ai --alerts
```

### 输出文件

| 文件 | 说明 |
|------|------|
| `data/recommendations.json` | 结构化分析结果（含 AI 任务包） |
| `data/reports/jingcai_*.md` | 每次运行生成的 Markdown 报告 |
| `data/final_recommendations.json` | 最终 AI 分析输出 |
| `data/final_reports/` | 归档报告，含索引 |
| `data/settlement_report.md` | 赛后准确率复盘报告 |

## AI 分析任务包

启用 `--ai` 参数后，Python 会生成结构化的分析任务包，交由 AI 大模型推理：

```json
{
  "status": "ready_for_openclaw_analysis",
  "candidate_bets": [...],      // Top 6 候选方向（含评分拆解）
  "team_profiles": {...},       // 主客队 10 维能力对比
  "search_queries": [...],      // 建议联网搜索关键词
  "candidate_pool_policy": {...}, // 动态刷新触发条件与规则
  "odds_movement": {...}         // 上次运行以来的显著指数变动
}
```

AI 智能体读取任务包后，结合联网搜索生成最终预测报告。

## 动态刷新机制

候选池不是静态结论。系统在以下情况下自动重新评估：

- 竞彩官方赔率或让球数显著变化
- 赛前首发阵容确认
- 关键球员伤停、停赛或临场缺阵
- 小组积分和出线形势变化
- 距离开赛 24h / 6h / 1h 的例行刷新

## 赛后复盘

比赛结束后：

```bash
# 1. 复制赛果模板并录入实际比分
cp data/match_results.example.csv data/match_results.csv

# 2. 运行复盘（默认单注 ¥10）
python settle_results.py --stake 10
```

生成准确率报告，包含每条推荐的命中情况和聚合统计数据。

## Web 前端

项目包含一个 SaaS 风格的 Landing Page，托管于 GitHub Pages：

🌐 **在线演示**：https://nasplycc.github.io/worldcup-bet-2026/

功能特性：
- 黑夜 / 白天主题切换（默认黑夜模式）
- 赛事赛程浏览
- AI 分析演示聊天界面
- 定价方案展示（小组赛 / 全程通行证 / 单场体验）
- 响应式设计，移动端适配

截图预览页面：

| 页面 | URL 路径 |
|------|----------|
| 系统架构图 | `/architecture.html` |
| AI 分析任务包数据 | `/ai-analysis-pack.html` |
| AI 聊天演示 | `/ai-demo.html` |
| 技能文件 + 终端日志 | `/skill-and-log.html` |
| 分析报告示例 | `/report-preview.html` |
| 赛后复盘报告 | `/settlement-report.html` |
| 主题切换对比 | `/theme-compare.html` |

## 部署指南

### 前置要求

- Python 3.10+
- OpenClaw 实例（用于 AI 分析层）
- Telegram Bot Token（可选，用于消息推送）

### 环境变量

```bash
# 可选：Telegram 推送配置
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="your-chat-id"
```

### 定时任务

通过 Cron 设置自动每日分析：

```bash
# 示例：每 6 小时运行一次
0 */6 * * * cd /path/to/worldcup-bet-2026 && ./run_openclaw.sh --mode upcoming --days 3 --ai --json >> /var/log/worldcup.log 2>&1
```

## 许可证

本项目基于 [MIT 许可证](LICENSE) 开源。

---

*声明：本系统仅供数据研究和学习使用，不构成任何投注或投资建议。请理性看待数据分析结果。*
