# ⚽ WorldCup Bet 2026 — AI-Powered World Cup Prediction Engine

> A fully automated AI-driven match prediction system for the 2026 FIFA World Cup, powered by Python data pipelines + LLM reasoning.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![FIFA World Cup 2026](https://img.shields.io/badge/FIFA%20World%20Cup-2026-132257?logo=fifa&logoColor=white)](https://www.fifa.com/)
[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Live-181717?logo=githubpages&logoColor=white)](https://nasplycc.github.io/worldcup-bet-2026/)

---

## 🚀 Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/nasplycc/worldcup-bet-2026.git
cd worldcup-bet-2026

# 2. Install dependencies
pip install -r requirements.txt

# 3. Validate data
python main.py --validate-data

# 4. Run analysis
./run_openclaw.sh --mode upcoming --days 45 --ai --json
```

## 📖 Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
- [Project Structure](#project-structure)
- [Usage](#usage)
- [AI Analysis Pack](#ai-analysis-pack)
- [Dynamic Refresh](#dynamic-refresh)
- [Settlement & Review](#settlement--review)
- [Web Frontend](#web-frontend)
- [Deployment](#deployment)
- [License](#license)

---

## Overview

**WorldCup Bet 2026** is a two-layer AI prediction system designed for the 2026 FIFA World Cup:

| Layer | Role |
|-------|------|
| **Python** | Data pipeline — syncs fixtures, collects market odds, builds 10-dimension team profiles, generates candidate directions |
| **AI (LLM)** | Decision engine — searches latest news, analyzes injuries/lineups/tactics, combines with data to produce final predictions |

The key design principle: **Python scripts contain zero AI model API keys**. Python only prepares structured data and hands it off to the AI agent, which performs the reasoning.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    DATA LAYER (Python)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ FIFA     │ │ Market   │ │ Team     │ │ Odds       │ │
│  │ Fixtures │ │ Odds     │ │ Profiles │ │ Movements  │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘ │
└───────┼────────────┼────────────┼──────────────┼────────┘
        │            │            │              │
        ▼            ▼            ▼              ▼
┌─────────────────────────────────────────────────────────┐
│                  ENGINE LAYER (Python)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ Rules    │ │ Feature  │ │ Candidate│ │ Refresh    │ │
│  │ Engine   │ │ Extract  │ │ Pool     │ │ Strategy   │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘ │
└───────┼────────────┼────────────┼──────────────┼────────┘
        │            │            │              │
        ▼            ▼            ▼              ▼
┌─────────────────────────────────────────────────────────┐
│                     AI LAYER (LLM)                       │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Web Search → News / Injuries / Lineups / Tactics │   │
│  │  Multi-dimensional Cross-Validation               │   │
│  │  Final Structured Report (Markdown + JSON)        │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│                    OUTPUT LAYER                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ Reports  │ │ Web UI   │ │ Telegram │ │ Settlement │ │
│  │ (MD/JSON)│ │ (GitHub  │ │ Push     │ │ & Review   │ │
│  │          │ │  Pages)  │ │          │ │            │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘ │
└─────────────────────────────────────────────────────────┘
```

## Features

- **104 matches** — Full FIFA World Cup 2026 schedule synchronized
- **48 team profiles** — 10-dimension capability model (attack, defense, experience, squad depth, upset potential, volatility, etc.)
- **Real-time odds tracking** — Automatic detection of significant market movements
- **AI-powered reasoning** — LLM performs live web search for injuries, lineups, tactics, weather
- **Dynamic candidate refresh** — Predictions auto-adjust when pre-match information changes
- **Automated settlement** — Post-match result entry → automatic accuracy statistics
- **Telegram alerts** — Real-time push notifications for important updates
- **Web frontend** — SaaS-style landing page hosted on GitHub Pages

## Project Structure

```
worldcup-bet-2026/
├── main.py                  # Entry point — orchestrate data + AI pipeline
├── config.json              # Configuration (plays, strategy, risk, alerts)
├── requirements.txt         # Python dependencies
├── run_openclaw.sh          # Shell wrapper for OpenClaw flow
│
├── ai_analysis.py           # Build AI analysis task packs
├── strategy.py              # Match analysis + parlay builder
├── jingcai.py               # Jingcai recommendation engine (SPF/RQSPF/JQS)
├── odds_tracking.py         # Odds movement detection & threshold alerts
├── odds_sources.py          # Market data validation
├── teams.py                 # Team profile builder (10-dimension model)
├── schedule.py              # Match filtering & loading
├── sync_schedule.py         # FIFA official schedule sync
├── fifa_schedule.py         # FIFA data source handler
├── report.py                # Markdown report renderer
├── settlement.py            # Post-match settlement engine
├── settle_results.py        # Result settlement CLI
├── archive_final.py         # Report archiver with index
├── alerts.py                # Telegram notification sender
├── state.py                 # JSON state persistence
├── SKILL.md                 # AI skill description (natural language instructions)
├── CRON.md                  # Cron job documentation
│
├── data/                    # Data directory
│   ├── jingcai_matches.csv  # Official jingcai fixtures & odds
│   ├── worldcup_2026_schedule.json  # FIFA 104-match fallback schedule
│   ├── teams.json           # 48-team 10-dimension profiles
│   ├── recommendations.json # Latest analysis output
│   ├── reports/             # Generated Markdown reports
│   ├── final_reports/       # Archived final reports + index
│   ├── odds_snapshot.json   # Current odds snapshot
│   ├── odds_movements.json  # Significant odds changes
│   └── final_recommendations.schema.json  # Output schema
│
└── docs/                    # GitHub Pages frontend
    ├── index.html           # Landing page (dark/light theme)
    ├── architecture.html    # Architecture diagram
    ├── ai-analysis-pack.html # AI analysis pack data preview
    ├── ai-demo.html         # AI chat demo preview
    ├── skill-and-log.html   # Skill file + terminal log preview
    ├── report-preview.html  # Analysis report preview
    ├── settlement-report.html # Settlement report preview
    └── theme-compare.html   # Theme comparison preview
```

## Usage

### Basic Commands

```bash
# Validate data integrity
python main.py --validate-data

# Analyze today's matches
./run_openclaw.sh --mode today --ai --json

# Analyze upcoming matches (next 3 days)
./run_openclaw.sh --mode upcoming --days 3 --ai --json

# Analyze specific matches from CSV
./run_openclaw.sh --mode upcoming --matches data/jingcai_matches.csv --ai --json

# Enable Telegram alerts
./run_openclaw.sh --mode upcoming --days 3 --ai --alerts
```

### Output

| File | Description |
|------|-------------|
| `data/recommendations.json` | Structured analysis with AI task packs |
| `data/reports/jingcai_*.md` | Markdown reports per run |
| `data/final_recommendations.json` | Final AI analysis output |
| `data/final_reports/` | Archived reports with index |
| `data/settlement_report.md` | Post-match accuracy review |

## AI Analysis Pack

When `--ai` flag is enabled, Python generates a structured analysis task pack for the LLM:

```json
{
  "status": "ready_for_openclaw_analysis",
  "candidate_bets": [...],      // Top 6 candidate directions with scores
  "team_profiles": {...},       // 10-dimension home/away comparison
  "search_queries": [...],      // Recommended web search keywords
  "candidate_pool_policy": {...}, // Dynamic refresh triggers & rules
  "odds_movement": {...}         // Significant odds changes since last run
}
```

The LLM uses this pack along with live web search to produce the final prediction.

## Dynamic Refresh

The candidate pool is **not a static conclusion**. The system automatically re-evaluates when:

- Market odds or handicap lines change significantly
- Starting lineups are confirmed
- Key player injuries, suspensions, or late scratches
- Group standings and qualification scenarios shift
- Weather or venue conditions change unfavorably
- Routine checks at 24h / 6h / 1h before kickoff

## Settlement & Review

After a match concludes:

```bash
# 1. Copy results template and enter actual scores
cp data/match_results.example.csv data/match_results.csv

# 2. Run settlement review (default stake: ¥10)
python settle_results.py --stake 10
```

This generates an accuracy report with per-recommendation results and aggregate statistics.

## Web Frontend

The project includes a SaaS-style landing page hosted on GitHub Pages:

🌐 **Live Demo**: https://nasplycc.github.io/worldcup-bet-2026/

Features:
- Dark / Light theme toggle (default: dark)
- Match schedule browser
- AI analysis demo chat interface
- Pricing plans (Group Stage / Full Pass / Single Match)
- Responsive design for mobile

Additional preview pages for documentation screenshots:

| Page | URL Path |
|------|----------|
| Architecture Diagram | `/architecture.html` |
| AI Analysis Pack Data | `/ai-analysis-pack.html` |
| AI Chat Demo | `/ai-demo.html` |
| Skill File + Terminal Log | `/skill-and-log.html` |
| Analysis Report Preview | `/report-preview.html` |
| Settlement Report Preview | `/settlement-report.html` |
| Theme Comparison | `/theme-compare.html` |

## Deployment

### Prerequisites

- Python 3.10+
- OpenClaw instance (for AI analysis layer)
- Telegram bot token (optional, for alerts)

### Environment

```bash
# Optional: Telegram alert configuration
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="your-chat-id"
```

### Scheduled Runs

Set up a cron job for automated daily analysis:

```bash
# Example: Run every 6 hours
0 */6 * * * cd /path/to/worldcup-bet-2026 && ./run_openclaw.sh --mode upcoming --days 3 --ai --json >> /var/log/worldcup.log 2>&1
```

## License

This project is open-sourced under the [MIT License](LICENSE).

---

*Disclaimer: This system is for data research and learning purposes only. It does not constitute any betting or investment advice. Please view data analysis results rationally.*
