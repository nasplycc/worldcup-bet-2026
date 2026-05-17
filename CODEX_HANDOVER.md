# 🤖 Codex 对接文档 — WorldCup Bet 2026

> 生成时间：2026-05-17 | 部署状态：✅ Docker 运行中

---

## 一、项目概述

**仓库：** `https://github.com/nasplycc/worldcup-bet-2026`
**定位：** 2026 年 FIFA 世界杯 AI 竞彩预测系统（双层架构）
- **Python 数据管道**：赛程、赔率、球队画像、规则引擎、候选投注生成
- **AI 大模型推理层**：OpenClaw 联网搜索伤停/阵容/战术，结合数据做最终预测

**核心设计原则：Python 脚本中不配置任何 AI 模型的 API Key。** Python 只准备结构化数据（AI 分析任务包），由 AI 智能体完成推理。

---

## 二、NAS 部署环境

### 2.1 Docker 部署

```yaml
# docker-compose.yml
services:
  worldcup-ai:
    build: .
    container_name: worldcup-bet-2026
    restart: unless-stopped
    ports:
      - "8088:8088"
    volumes:
      - ./data:/app/data          # 数据持久化
      - ./.env:/app/.env:ro       # 环境变量
    env_file:
      - .env
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8088/api/health')"]
      interval: 60s
      timeout: 10s
      retries: 3
```

### 2.2 运行状态

| 项目 | 状态 |
|------|------|
| 容器名 | `worldcup-bet-2026` |
| 端口 | NAS:8088 → 容器:8088 |
| Python 版本 | 3.11 (slim) |
| Flask | 3.1.3 (threaded=True) |
| 依赖 | python-dotenv, requests, flask |
| 健康检查 | ✅ healthy |

### 2.3 重要路径

| 路径 | 说明 |
|------|------|
| `/vol1/1000/github/worldcup-bet-2026/` | NAS 本地项目根目录 |
| `./data/` | 数据目录（volume 挂载到容器 `/app/data`） |
| `./docs/` | GitHub Pages 前端静态站 |
| `./Dockerfile` | 镜像构建定义 |
| `./docker-compose.yml` | 容器编排配置 |
| `./.env` | 环境变量（API Key，gitignore） |

### 2.4 数据文件清单

```
data/
├── worldcup_2026_schedule.json   # ✅ FIFA 104 场完整赛程
├── teams.json                    # ✅ 50 支球队 10 维画像
├── recommendations.json          # ✅ 规则引擎分析输出（2 场比赛，10 个候选）
├── odds_snapshot.json            # 赔率快照（示例数据）
├── odds_movements.json           # 赔率变动记录
├── jingcai_matches.csv           # ⚠️ 仅 2 行示例，需替换真实赔率
├── final_recommendations.json    # ⚠️ 需 AI 分析后生成
├── final_recommendations.md      # ⚠️ 需 AI 分析后生成
├── final_recommendations.schema.json  # 输出结构定义
├── reports/                      # 29 份 MD 报告
├── final_reports/                # 归档报告 + index.json
└── match_results.example.csv     # 赛后复盘模板
```

**⚠️ 不需要数据库！** 项目纯用 JSON/CSV 文件存储数据，无 MySQL/PostgreSQL/Redis 等数据库依赖。

---

## 三、API 接口（Flask 后端）

### 3.1 基础接口

```
GET /                          → 前端 Landing Page
GET /api/health                → 健康检查 + 数据加载状态
GET /api/system/status         → 各模块运行状态
```

### 3.2 世界杯赛事接口

```
GET /api/worldcup/matches      → 世界杯赛程 + 推荐分析
GET /api/worldcup/recommendations  → 原始推荐 JSON
```

### 3.3 英超赛事接口（已有，待接入竞彩分析）

```
GET /api/epl/matches           → 英超赛程 + 赛果
GET /api/epl/standings         → 英超积分榜
GET /api/epl/top-scorers       → 射手榜
```

### 3.4 统一接口

```
GET /api/matches               → 世界杯 + 英超合并数据
```

### 3.5 响应示例

```bash
# 健康检查
curl http://NAS_IP:8088/api/health
```

```json
{
  "ok": true,
  "football_data_api_key": true,
  "epl_matches": 380,
  "epl_status": 200,
  "worldcup_matches": 104,
  "worldcup_status": 200,
  "source": "combined",
  "updated": "2026-05-17T07:29:46.374925+08:00"
}
```

---

## 四、前端（GitHub Pages）

```
docs/
├── index.html              # Landing Page（黑夜/白天模式，SaaS 风格）
├── architecture.html       # 系统架构图
├── ai-analysis-pack.html   # AI 分析任务包数据预览
├── ai-demo.html           # AI 聊天演示界面
├── skill-and-log.html     # 技能文件 + 终端日志预览
├── report-preview.html    # 分析报告示例
├── settlement-report.html # 赛后复盘报告示例
├── theme-compare.html     # 主题切换效果对比
└── data/                  # 静态数据（已过时，改用 API）
```

**在线演示：** `https://nasplycc.github.io/worldcup-bet-2026/`
**NAS 本地访问：** `http://NAS_IP:8088`

---

## 五、核心工作流

### 5.1 Python 数据管道（main.py）

```
1. 加载配置（config.json）
2. 加载赛程数据（CSV 优先 → JSON 回退）
3. 数据校验（validate_matches）
4. 筛选比赛（today / upcoming N天 / all）
5. 加载球队画像（teams.json）
6. 逐场比赛分析：
   ├── 规则引擎 → SPF / RQSPF / JQS 推荐 + 信心评分
   └── AI 任务包 → 候选投注评分 + 搜索关键词 + 刷新策略
7. 赔率变动检测
8. 构建混合过关组合（confidence ≥ 0.62）
9. 生成 Markdown 报告
10. 输出 JSON 结果
```

### 5.2 AI 分析流程（OpenClaw）

```
Python: --ai → 生成 AI 分析任务包（data/recommendations.json）
    ↓
OpenClaw: 读取任务包 → 联网搜索（伤停/阵容/战术/天气/新闻）
    ↓
OpenClaw: 结合球队画像 + 赔率 + 搜索结果做推理
    ↓
OpenClaw: 输出 data/final_recommendations.md + .json
    ↓
OpenClaw: 执行 python archive_final.py 归档
```

### 5.3 赛后复盘

```
1. 录入实际比分到 data/match_results.csv
2. python settle_results.py --stake 10
3. 输出 data/settlement_report.md + .json
```

---

## 六、运行命令

```bash
# 进入项目目录
cd /vol1/1000/github/worldcup-bet-2026

# Docker 相关
docker compose up -d          # 启动
docker compose down           # 停止
docker compose logs -f        # 查看日志
docker compose up -d --build  # 重建镜像

# 容器内执行
docker exec worldcup-bet-2026 python main.py --validate-data
docker exec worldcup-bet-2026 python main.py --mode upcoming --days 45 --ai --json

# 本地执行（不通过 Docker）
pip install -r requirements.txt
python main.py --validate-data
./run_openclaw.sh --mode upcoming --days 3 --ai --json

# 赛后复盘
python settle_results.py --stake 10
```

---

## 七、配置与策略参数（config.json）

| 参数 | 值 | 说明 |
|------|-----|------|
| `confidence_threshold` | 0.56 | 最低信心阈值 |
| `strong_confidence` | 0.68 | 强信心阈值 |
| `max_parlay_matches` | 4 | 混合过关最多场次 |
| `parlay_min_confidence` | 0.62 | 过关最低信心 |
| `home_advantage` | 0.035 | 主场优势系数 |
| `risk.style` | aggressive | 高风险高收益 |

**竞彩代码：** 胜平负 `3=主胜 1=平 0=主负`；让球 `3=让胜 1=让平 0=让负`；总进球 `0-7+`

---

## 八、项目文件职责

```
├── main.py                 # 入口 — 编排完整工作流
├── config.json             # 全局配置
├── server.py               # Flask 后端 API + 前端静态文件服务
├── frontend_data.py        # 前端数据格式转换 + 赛程/推荐合并
├── export_frontend_data.py # 导出静态 JSON 供 GitHub Pages 使用
│
├── odds_sources.py         # 数据加载（CSV/JSON）+ 校验
├── schedule.py             # 比赛筛选
├── fifa_schedule.py        # FIFA 赛程处理
├── sync_schedule.py        # FIFA 赛程同步
│
├── teams.py                # 球队画像加载
├── strategy.py             # 比赛分析 + 混合过关
├── jingcai.py              # 竞彩推荐引擎（SPF/RQSPF/JQS）
│
├── ai_analysis.py          # AI 分析任务包构建
├── odds_tracking.py        # 赔率变动追踪
├── report.py               # Markdown 报告渲染
├── settlement.py           # 赛后结算引擎
├── settle_results.py       # 赛后复盘 CLI
├── archive_final.py        # 报告归档
├── alerts.py               # Telegram 推送
├── state.py                # JSON 状态持久化
```

---

## 九、当前状态与待开发任务

### ✅ 已完成
- FIFA 104 场赛程 + 50 支球队画像
- 竞彩推荐引擎（SPF/RQSPF/JQS/混合过关）
- AI 分析任务包构建
- Flask 后端 API（世界杯 + 英超数据）
- Docker Compose 部署
- GitHub Pages 前端

### 🔴 高优先级
1. **真实竞彩赔率数据接入** — CSV 仅示例数据，需接入真实赔率
2. **AI 分析闭环自动化** — 当前半手动，建议定时调度 + 自动归档
3. **前端动态化** — GitHub Pages 纯静态，需接入后端 API
4. **server.py 扩展世界杯分析** — 英超 API 已有，世界杯分析未接入

### 🟡 中优先级
5. 多数据源赔率对比
6. 球队画像动态更新（世界杯临近时）
7. 实时赔率监控 + 告警
8. Telegram Bot 交互完善
9. 赛后复盘自动化
10. 历史回测优化参数

---

## 十、环境变量

```bash
# .env（已挂载到容器，不要提交到 Git）
FOOTBALL_DATA_API_KEY=c2b749…f68e
TELEGRAM_BOT_TOKEN=（可选）
TELEGRAM_CHAT_ID=（可选）
```

---

## 十一、开发注意事项

1. **无数据库** — 所有数据在 `data/` 目录的 JSON/CSV 文件中
2. **Python 不配 AI Key** — 保持数据管道与 AI 推理分离
3. **激进策略** — 高风险高收益，不做保守稳胆
4. **结构化输出** — 所有分析结果可被机器消费（JSON）
5. **Docker volume** — `data/` 目录通过 volume 挂载，容器重建不丢数据
6. **Git 分支** — `main` 分支，直接推送即可

---

*交接完毕。祝开发顺利！⚽*
