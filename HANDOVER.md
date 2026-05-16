# 📋 项目交接文档 — WorldCup Bet 2026

> 创建时间：2026-05-16 | 交接对象：Codex（后续开发）
> 最后更新：2026-05-17 — 补充 Docker Compose 部署方案

---

## 一、项目概览

### 1.1 基本信息

| 项目 | 信息 |
|------|------|
| **仓库** | `https://github.com/nasplycc/worldcup-bet-2026` |
| **本地路径** | `/vol1/1000/github/worldcup-bet-2026` |
| **主分支** | `main`（5 次提交） |
| **语言** | Python 3.10+ |
| **License** | MIT |
| **在线演示** | `https://nasplycc.github.io/worldcup-bet-2026/` |

### 1.2 项目定位

面向 2026 年 FIFA 世界杯（北美三国合办，48 队参赛）的**双层 AI 竞彩预测系统**：

- **Python 数据管道**：赛程同步、市场指数采集、10 维球队画像、候选投注方向生成
- **AI 大模型推理层**：联网搜索最新伤停/阵容/战术/天气信息，结合数据模型做最终预测

核心设计原则：**Python 脚本中不配置任何 AI 模型的 API Key**。Python 只准备结构化数据（AI 分析任务包），由 AI 智能体（OpenClaw）完成推理判断。

### 1.3 核心数据

- **104 场赛事**：FIFA 官方完整赛程（小组赛 72 场 + 淘汰赛 32 场）
- **48+ 支球队画像**：10 维能力模型（攻击、防守、大赛经验、阵容深度、冷门潜力、波动性等）
- **竞彩玩法**：胜平负（SPF）、让球胜平负（RQSPF）、总进球（JQS）、混合过关
- **风险风格**：aggressive（高风险高收益，优先找赔率价值和搏冷门）

---

## 二、当前状态

### 2.1 已完成功能 ✅

| 模块 | 状态 | 说明 |
|------|------|------|
| 赛程数据 | ✅ 完成 | FIFA 104 场完整赛程 JSON；CSV 导入模板（含示例赔率） |
| 球队画像 | ✅ 完成 | 50 支球队 10 维数据（含 elite/strong/solid/mid/underdog 分档） |
| 规则引擎 | ✅ 完成 | SPF/RQSPF/JQS 三种玩法的概率模型 + 市场赔率混合 |
| 指数追踪 | ✅ 完成 | 快照比对、阈值检测（绝对值 ≥0.05 或百分比 ≥3%） |
| AI 任务包 | ✅ 完成 | 生成结构化 JSON 任务包（候选投注、搜索关键词、刷新策略） |
| 报告生成 | ✅ 完成 | Markdown 报告 + 自动归档 |
| 赛后复盘 | ✅ 完成 | 赛果录入 → 自动结算准确率 → 生成复盘报告 |
| Telegram 推送 | ✅ 完成 | 可选开关，支持消息推送 |
| Web 前端 | ✅ 完成 | GitHub Pages 静态站（黑夜/白天模式，7 个子页面） |
| 后端 API | 🟡 部分完成 | Flask server（英超赛事 API，暂未接入世界杯分析） |

### 2.2 Git 状态

```
Branch: main (up to date with origin/main)
Unstaged: docs/index.html (modified)
Untracked: .env, server.py
```

**最近 5 次提交：**
1. `aec9bba` — 中文 README 默认，英文移至 README.en.md
2. `852dcbc` — README 重写 + 截图预览页
3. `682c43f` — 前端紧凑化调整
4. `f57bf77` — 前端移至 docs/ 目录适配 GitHub Pages
5. `460673b` — AI 赛事分析系统 v0.1 完整初始化

### 2.3 数据完整性

| 文件 | 状态 | 说明 |
|------|------|------|
| `data/jingcai_matches.csv` | ⚠️ 示例数据 | 仅 2 行示例，需替换为真实竞彩赔率 |
| `data/worldcup_2026_schedule.json` | ✅ 完整 | FIFA 104 场完整赛程 |
| `data/teams.json` | ✅ 基本完整 | 50 支球队画像（含 48 支参赛队 + 2 支示例） |
| `data/odds_snapshot.json` | ⚠️ 示例数据 | 仅示例快照，无真实变动记录 |
| `data/final_recommendations.json` | ⚠️ 未生成 | 需要 AI 分析后产出 |
| `data/final_reports/` | ⚠️ 1 次示例归档 | 仅一次测试归档 |

---

## 三、项目结构与文件职责

### 3.1 核心 Python 模块

```
├── main.py                 # 入口 — 编排数据加载 → 规则分析 → AI 任务包 → 报告 → 推送
├── config.json             # 全局配置（玩法、策略参数、风险、赔率追踪阈值、推送开关）
├── requirements.txt        # 依赖：python-dotenv, requests
├── run_openclaw.sh         # Shell 封装，统一参数解析
│
├── odds_sources.py         # 数据加载（CSV/JSON 双格式）、数据校验
├── schedule.py             # 比赛筛选（today / upcoming / all / parlay）
├── fifa_schedule.py        # FIFA 赛程数据源处理
├── sync_schedule.py        # FIFA 赛程同步脚本
│
├── teams.py                # 球队画像加载 + 未知球队兜底
├── strategy.py             # 比赛分析（analyze_match）+ 混合过关（build_parlay）
├── jingcai.py              # 竞彩推荐引擎：
│   ├── recommend_spf()     #   胜平负 — 概率模型 + 市场赔率混合（55:45）
│   ├── recommend_rqspf()   #   让球胜平负 — 让球计算 +  handicap 信心惩罚
│   └── recommend_jqs()     #   总进球 — 基于市场赔率重心
│
├── ai_analysis.py          # AI 分析任务包构建：
│   ├── local_value_candidates()  # 候选投注评分（赔率回报 + 信心 + 对阵匹配 + 冷门价值 - 风险惩罚）
│   ├── search_queries()          # 联网搜索关键词生成
│   ├── candidate_pool_policy()   # 候选池刷新策略
│   └── build_openclaw_analysis_pack()  # 组装完整任务包
│
├── odds_tracking.py        # 赔率变动追踪（快照比对 + 阈值检测 + 显著变化记录）
├── report.py               # Markdown 报告渲染 + 自动归档
├── settlement.py           # 赛后结算引擎（赛果匹配 → 命中计算 → ROI 统计）
├── settle_results.py       # 赛后复盘 CLI
├── archive_final.py        # 报告归档 + 索引管理
├── alerts.py               # Telegram 推送
├── state.py                # JSON 状态持久化（load/save）
│
└── server.py               # Flask 后端（英超赛事 API，尚未接入世界杯分析）
    └── /api/epl/matches    #   英超剩余赛程 + 近 7 天赛果
    └── /api/epl/standings  #   英超积分榜
    └── /api/epl/top-scorers #  射手榜
```

### 3.2 数据目录

```
data/
├── jingcai_matches.csv          # 竞彩赔率 CSV 主入口（⚠️ 当前为示例数据）
├── jingcai_matches.example.csv  # CSV 模板
├── worldcup_2026_schedule.json  # FIFA 104 场完整赛程
├── teams.json                   # 48+ 支球队画像
├── recommendations.json         # 最新分析输出
├── final_recommendations.json   # AI 最终分析输出（需 AI 生成）
├── final_recommendations.schema.json  # 输出结构定义
├── odds_snapshot.json           # 当前赔率快照
├── odds_movements.json          # 显著变动记录
├── match_results.example.csv    # 赛果录入模板
├── reports/                     # 每次运行生成的 MD 报告（27 份测试报告）
└── final_reports/               # 归档报告 + index.json
```

### 3.3 前端

```
docs/                          # GitHub Pages 静态站
├── index.html                 # Landing Page（黑夜/白天模式，SaaS 风格）
├── architecture.html          # 系统架构图
├── ai-analysis-pack.html      # AI 分析任务包数据预览
├── ai-demo.html              # AI 聊天演示界面
├── skill-and-log.html        # 技能文件 + 终端日志预览
├── report-preview.html       # 分析报告示例
├── settlement-report.html    # 赛后复盘报告预览
└── theme-compare.html        # 主题切换效果对比
```

---

## 四、核心工作流

### 4.1 主流程（`main.py`）

```
1. 加载配置（config.json）
2. 加载赛程数据（CSV 优先 → JSON 回退）
3. 数据校验（validate_matches）
4. 筛选比赛（today / upcoming N天 / all）
5. 加载球队画像（teams.json）
6. 逐场比赛分析：
   ├── 规则引擎 → SPF / RQSPF / JQS 推荐 + 信心评分
   └── AI 任务包 → 候选投注评分 + 搜索关键词 + 刷新策略
7. 赔率变动检测（与上次快照比对）
8. 构建混合过关组合（confidence ≥ 0.62，最多 4 场）
9. 生成 Markdown 报告 → 保存 → 可选 Telegram 推送
10. 输出 JSON 结果（--json 参数）
```

### 4.2 AI 分析流程（OpenClaw）

```
Python: --ai → 生成 AI 分析任务包（ready_for_openclaw_analysis）
    ↓
OpenClaw: 读取 data/recommendations.json
    ↓
OpenClaw: 联网搜索（伤停、阵容、战术、天气、新闻）
    ↓
OpenClaw: 结合球队画像 + 赔率 + 搜索结果做推理
    ↓
OpenClaw: 输出 data/final_recommendations.md + data/final_recommendations.json
    ↓
OpenClaw: 执行 python archive_final.py 归档
```

### 4.3 赛后复盘流程

```
1. 复制模板：cp data/match_results.example.csv data/match_results.csv
2. 录入实际比分
3. python settle_results.py --stake 10
4. 输出 data/settlement_report.md + data/settlement_report.json
```

---

## 五、配置与策略参数

### 5.1 策略参数（config.json → strategy）

| 参数 | 值 | 说明 |
|------|-----|------|
| `default_lookahead_days` | 45 | 默认前瞻天数 |
| `max_matches_per_report` | 12 | 每份报告最多比赛数 |
| `confidence_threshold` | 0.56 | 最低信心阈值 |
| `strong_confidence` | 0.68 | 强信心阈值 |
| `max_parlay_matches` | 4 | 混合过关最多场次 |
| `parlay_min_confidence` | 0.62 | 过关最低信心 |
| `home_advantage` | 0.035 | 主场优势系数 |
| `rating_scale` | 18.0 | 评分缩放因子 |
| `draw_base` | 0.27 | 基础平局概率 |
| `handicap_confidence_penalty` | 0.08 | 让球信心惩罚 |

### 5.2 风险参数

- **风格**：`aggressive`（高风险高收益）
- **偏好**：寻找高赔率价值和搏冷门机会
- **不做保守稳胆**

### 5.3 赔率追踪阈值

- **绝对变化**：≥ 0.05 触发记录
- **百分比变化**：≥ 3% 触发记录

---

## 六、后续待开发任务

### 6.1 高优先级 🔴

| # | 任务 | 当前状态 | 说明 |
|---|------|----------|------|
| 1 | **真实竞彩数据接入** | ⚠️ 仅示例 | 需接入真实竞彩官方赔率数据，替换 CSV 示例数据。可考虑：竞彩官网爬取、第三方 API、手动录入 |
| 2 | **AI 分析闭环自动化** | 🟡 半自动 | 当前 AI 分析包生成后需手动触发 OpenClaw 推理。建议：自动调度 → OpenClaw 分析 → 自动归档全流程 |
| 3 | **前端数据动态化** | 🔴 静态 | GitHub Pages 纯静态，不显示实时分析结果。建议：接入后端 API 或改为 SSR/SSG |
| 4 | **server.py 扩展** | 🟡 仅英超 | 当前 Flask 后端只有英超 API，需扩展世界杯赛事分析接口 |

### 6.2 中优先级 🟡

| # | 任务 | 说明 |
|---|------|------|
| 5 | **多数据源聚合** | 接入多家赔率数据源做对比，增强推荐准确性 |
| 6 | **球队画像动态更新** | 当前球队画像为静态数据，需根据世界杯前最新阵容、状态动态更新 |
| 7 | **实时赔率监控** | 增加定时任务，持续监控赔率变动，触发告警 |
| 8 | **Telegram Bot 完善** | 当前推送仅单向发送，可增加交互（查比赛、查推荐等） |
| 9 | **赛后复盘自动化** | 比赛结束后自动获取赛果并执行复盘 |
| 10 | **历史回测** | 用历史数据回测规则引擎准确性，优化参数 |

### 6.3 低优先级 🟢

| # | 任务 | 说明 |
|---|------|------|
| 11 | **移动端 App** | 考虑将前端封装为小程序或 App |
| 12 | **用户系统** | 付费订阅、个人推荐定制 |
| 13 | **社交功能** | 推荐分享、用户讨论区 |
| 14 | **多模型对比** | 同时调用多个 AI 模型做独立分析，交叉验证 |

---

## 七、关键技术细节

### 7.1 概率模型

**SPF 模型：**
1. 基于球队评分差（home_rating - away_rating）通过 Sigmoid 计算主胜概率
2. 非中立场加主场优势修正
3. 平局概率 = 基础值 + 双方实力接近度奖励
4. 最终概率 = 55% 模型 × 45% 市场赔率隐含概率

**RQSPF 模型：**
1. 先计算让球调整后的主客队实力差
2. 用规则判定（让胜/让平/让负）给规则分配高权重
3. 再与市场赔率混合

**JQS 模型：**
1. 主要依赖市场赔率隐含概率
2. 选择概率最大的进球数作为推荐

### 7.2 AI 任务包评分体系

每个候选投注按以下维度评分：
- **赔率回报**（Payout）：18-46 分（激进风格）
- **规则信心**（Confidence）：0-24 分
- **对阵匹配**（Matchup Fit）：基于 10 维球队画像
- **冷门价值**（Upset Value）：高赔 + 波动性 + 实力差
- **风险惩罚**（Risk Penalty）：超高赔、低信心、未知球队扣分

### 7.3 竞彩代码映射

```
胜平负：3=主胜，1=平，0=主负
让球胜平负：3=让胜，1=让平，0=让负
总进球：0/1/2/3/4/5/6/7+
```

---

## 八、环境与依赖

### 8.1 Python 依赖

```
python-dotenv>=1.0.1
requests>=2.32.0
flask（server.py 新增，尚未加入 requirements.txt）
```

### 8.2 环境变量

```bash
# Telegram 推送（可选）
TELEGRAM_BOT_TOKEN="your-bot-token"
TELEGRAM_CHAT_ID="your-chat-id"

# football-data.org API（server.py 使用）
FOOTBALL_DATA_API_KEY="c2b749da3c1744149d22c08dc5baf68e"
```

### 8.3 运行命令

```bash
# 数据校验
python main.py --validate-data

# 分析未来 N 天比赛
./run_openclaw.sh --mode upcoming --days 3 --ai --json

# 分析指定 CSV
./run_openclaw.sh --mode upcoming --matches data/jingcai_matches.csv --ai --json

# 启用推送
./run_openclaw.sh --mode upcoming --days 3 --ai --alerts

# 赛后复盘
python settle_results.py --stake 10
```

---

## 九、已知问题与注意事项

### 9.1 当前限制

1. **无真实数据**：竞彩赔率 CSV 仅有 2 行示例数据，世界杯开赛时间（2026.06.11）前无法获取真实竞彩赔率
2. **球队画像静态**：48 支球队画像基于赛前预估，世界杯临近时阵容变化需要更新
3. **AI 分析半自动**：需要手动运行脚本 → 手动触发 OpenClaw 分析 → 手动归档
4. **前端静态**：GitHub Pages 纯静态展示，不是实时数据应用
5. **server.py 未接入主流程**：Flask 后端独立运行，与世界杯分析管道未整合
6. **requirements.txt 缺失 flask**：server.py 新增但未更新依赖列表
7. **.env 文件未提交**：包含 API Key，已在 .gitignore 或应被忽略

### 9.2 时间线注意

- 世界杯开幕：**2026 年 6 月 11 日**
- 竞彩数据通常在赛前 1-2 周开始开售
- 建议：
  - 5 月底 - 6 月初：确认各队 23 人大名单，更新 `teams.json`
  - 开赛后：接入实时赛果做复盘
  - 每场比赛前：运行 AI 分析 + 动态刷新

### 9.3 免责声明

系统仅供数据研究使用，不构成任何投注或投资建议。

---

## 十、给 Codex 的起步建议

### 10.1 推荐第一步

```bash
# 1. 拉取最新代码
cd /vol1/1000/github/worldcup-bet-2026
git pull origin main

# 2. 安装依赖 + 校验环境
pip install -r requirements.txt
python main.py --validate-data

# 3. 了解数据流（运行一次，看输出结构）
./run_openclaw.sh --mode upcoming --days 45 --json
```

### 10.2 建议开发顺序

1. **补齐 requirements.txt**（加入 flask）
2. **server.py 接入世界杯分析**（将主流程结果通过 API 暴露）
3. **真实数据源接入方案**（设计 CSV 数据更新 pipeline）
4. **AI 分析自动化**（将 OpenClaw 分析集成到定时任务）
5. **前端动态化**（从后端 API 拉取数据渲染）
6. **球队画像更新机制**（世界杯临近时的动态维护）

### 10.3 设计原则保持

- **Python 不配 AI Key**：保持数据管道与 AI 推理分离
- **激进策略**：高风险高收益，不做保守稳胆
- **候选池动态**：不是静态结论，信息变化时重新评估
- **结构化输出**：所有分析结果可被机器消费（JSON）

---

*交接完毕。祝后续开发顺利！⚽*

---

## 十一、Docker Compose 部署方案（2026-05-17 补充）

### 11.1 可行性结论

**✅ 完全可行，且推荐。** 项目当前结构非常适合容器化部署：
- Flask `server.py` 已同时服务前端静态页 + API
- 依赖轻量（python-dotenv, requests, flask）
- 无数据库依赖，纯 JSON/CSV 文件存储
- NAS 上运行 `docker compose up -d` 即可

### 11.2 部署影响分析

| 维度 | 影响 | 处理方式 |
|------|------|----------|
| **端口占用** | 容器内监听 8088 | 确认 NAS 上 8088 未被占用；可在 `docker-compose.yml` 中映射为其他端口 |
| **数据持久化** | `data/` 目录存储赔率、推荐、报告等 | 通过 volume 挂载，容器重建不丢数据 |
| **环境变量** | `.env` 含 API Key（football-data.org） | `.env` 文件挂载进容器，不写入镜像 |
| **定时任务** | 定时运行 `main.py` 分析 + 赔率监控 | 方案一：容器内用 supervis/cron；方案二：宿主机 cron 调用 `docker exec` |
| **HTTPS/反向代理** | 若 NAS 有 Nginx Proxy Manager | 反向代理到容器 8088 即可 |
| **Python 版本** | 需要 Python 3.10+ | 镜像基于 `python:3.11-slim`，无需担心 |
| **GPU/加速** | 不需要 | 纯 CPU 运行 |

### 11.3 需要的文件

#### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 数据目录用 volume 挂载，不在镜像中
VOLUME /app/data

EXPOSE 8088

CMD ["python", "server.py"]
```

#### docker-compose.yml

```yaml
services:
  worldcup-ai:
    build: .
    container_name: worldcup-bet-2026
    restart: unless-stopped
    ports:
      - "8088:8088"
    volumes:
      - ./data:/app/data
      - ./.env:/app/.env:ro
    env_file:
      - .env
    # 如果需要定时分析，可以加 healthcheck
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8088/api/health"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 15s
```

### 11.4 部署步骤

```bash
# 1. 进入项目目录
cd /vol1/1000/github/worldcup-bet-2026

# 2. 确认 .env 文件存在且配置正确
cat .env

# 3. 构建并启动
docker compose up -d --build

# 4. 验证
curl http://localhost:8088/api/health
# 访问 http://NAS_IP:8088 查看前端

# 5. 查看日志
docker compose logs -f worldcup-ai
```

### 11.5 定时分析任务（宿主机 cron 方案）

```bash
# 在 NAS 的 crontab 中添加：
# 每 6 小时运行一次分析
0 */6 * * * cd /vol1/1000/github/worldcup-bet-2026 && docker compose exec -T worldcup-ai python main.py --mode upcoming --days 7 --ai --json >> /var/log/worldcup-analysis.log 2>&1

# 每天凌晨 2 点归档
0 2 * * * cd /vol1/1000/github/worldcup-bet-2026 && docker compose exec -T worldcup-ai python archive_final.py >> /var/log/worldcup-archive.log 2>&1
```

### 11.6 Codex 接手后的 Docker 优先任务

1. 创建 `Dockerfile` + `docker-compose.yml`
2. 本地 `docker compose up` 验证前端/API 是否正常
3. 考虑在容器内集成 `supervisord` 管理 Flask + 定时分析任务
4. 编写 `docker-compose.prod.yml` 覆盖（生产环境端口/资源限制等）
5. （可选）加入 `docker-compose.dev.yml` 用于开发调试（热重载等）
