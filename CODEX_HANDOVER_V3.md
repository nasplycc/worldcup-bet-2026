# 📋 Codex 交接文档 v3 — 剩余工作清单

> 创建时间：2026-05-17 14:40 GMT+8 | 交接对象：Codex
> 生成者：OpenClaw 资讯助手

---

## 一、当前环境状态

| 项目 | 状态 |
|------|------|
| **本地路径** | `/vol1/1000/github/worldcup-bet-2026` |
| **Docker 状态** | ✅ `worldcup-bet-2026` (Flask :8088) + `worldcup-bet-2026-db` (PostgreSQL 16) |
| **数据库表** | 10 张表：users, user_preferences, subscriptions, matches, teams, competitions, odds_snapshots, analysis_results, analysis_jobs, sync_runs |
| **Git 分支** | `main` (最新提交 `6778174`) |
| **访问地址** | `NAS_IP:8088` / `https://foot.nasply.site` |
| **管理员账号** | `zhoujabn@126.com` / `mikle210MIKLE` (role: admin) |

### 数据量

| 表 | 记录数 |
|------|--------|
| matches | 484 (世界杯 104 + 英超 380) |
| teams | 50 |
| odds_snapshots | ~637 (已去重) |
| analysis_jobs | 3 (queued) |
| users | 1 |

---

## 二、已完成工作

### 后端（Codex + OpenClaw）
- ✅ PostgreSQL 数据库 + 10 张表
- ✅ JWT 认证系统（注册/登录/鉴权）
- ✅ The Odds API + API-Football 双赔率数据源
- ✅ 自动同步后台任务（每 60 分钟）
- ✅ AI 分析管道框架（`ai_pipeline.py`，默认关闭）
- ✅ `db_sync.py` 赔率快照去重插入（已修复重复问题）
- ✅ 详情 API `GET /api/matches/{league}/{source_id}`
- ✅ 管理员同步接口 `POST /api/admin/sync`
- ✅ 启动时 DB 连接重试（5 次）

### 前端（OpenClaw 修改）
- ✅ 筛选器简化为 4 个快捷按钮（📅 近 7 天 / 🔥 有指数 / ✅ 已结束 / 🔍 全部）
- ✅ 联赛 Tab 样式修复
- ✅ 英超轮次排序修复（数字排序而非字符串）
- ✅ 指数历史中文化（大小球/主胜/平局/客胜，去重，去机构名）
- ✅ 比赛信息空值占位（"未公布"）
- ✅ 实时指数简化显示（主/平/客 赔率数字）
- ✅ 时间格式本地化（北京时间 24h）
- ✅ 数据源/博彩机构名称中文化

### 环境配置
- ✅ `.env` 已配置完整注释（8 个模块）
- ✅ The Odds API Key / API-Football Key / football-data.org Key 已配置
- ✅ Docker Compose 包含 Flask + PostgreSQL

---

## 三、待完成工作（优先级排序）

### 🔴 高优先级

#### 1. 前端详情面板优化
**当前问题：**
- 实时指数只显示一个来源的赔率（The Odds API），用户想看多来源对比
- 指数历史现在只显示去重后的快照，但格式不够直观
- 实时指数和指数历史的视觉区分不明显

**建议方案：**
- 实时指数：如果多来源有数据，用 tab 或下拉切换
- 指数历史：只显示最近一次采集的快照，用更清晰的表格展示
- 考虑去掉"指数历史"模块，只在赔率有变动时才显示历史

#### 2. 赔率数据覆盖率优化
**现状：**
- 英超 380 场只有 9 场有赔率（The Odds API 只返回近 7 天比赛）
- 世界杯 104 场只有 12 场有赔率
- API-Football 的赔率数据也未有效利用（`fetch_api_football_odds` 函数存在但未充分使用）

**建议方案：**
- 完善 `fetch_api_football_odds` 的调用逻辑（当前只 fallback 到它）
- 优化 `odds_team_key` 的球队名匹配（当前精确匹配日期+队名，容易对不上）
- 考虑增加第三方赔率数据源或历史赔率 API

#### 3. AI 分析功能启用
**现状：**
- `ai_pipeline.py` 已写好但 `AI_ANALYSIS_ENABLED=false`
- `OPENAI_API_KEY` 为空
- 3 个分析任务排队等待

**建议方案：**
- 接入 OpenAI 兼容 API（用户可能有其他模型提供商）
- 实现 `analysis_jobs` 的自动执行逻辑
- 分析结果写入 `analysis_results` 表后在前端展示

### 🟡 中优先级

#### 4. 前端体验优化
- 比赛卡片点击后的详情加载速度优化（当前调用 `/api/matches/{league}/{id}` 较慢）
- 空状态页面优化（无赔率比赛显示更友好的提示）
- 手机端适配优化（卡片在小屏幕上布局紧凑）
- "近 7 天"筛选窗口说明文案（用户可能不理解为什么只看到少量比赛）

#### 5. 用户系统完善
- 当前只有注册/登录，没有用户个人中心页面
- 没有收藏比赛、投注记录等功能
- `subscriptions` 表已创建但未使用

#### 6. 赛后复盘功能
- `settle_results.py` 和 `settlement.py` 已存在但未接入前端
- 没有赛果自动录入功能
- 准确率统计页面缺失

### 🟢 低优先级

#### 7. 定时任务优化
- 当前同步是 60 分钟一次，对赔率监控来说太慢
- 没有赔率变动告警功能
- AI 分析未与同步任务联动

#### 8. 数据可视化
- 无赔率趋势图
- 球队实力雷达图
- 历史准确率统计图表

#### 9. 管理后台
- 用户管理
- 数据源配置
- 同步日志查看
- AI 分析任务管理

---

## 四、代码结构

```
├── server.py               # Flask 后端 API（主入口）
├── db.py                   # SQLAlchemy 模型 + DB 初始化
├── db_sync.py              # 数据同步到 DB（赛程、赔率、分析结果）
├── ai_pipeline.py          # AI 分析管道（待启用）
├── sync_env.py             # .env.example → .env 变量同步工具
├── main.py                 # 命令行数据管道入口
├── config.json             # 策略配置
├── requirements.txt        # Python 依赖
├── .env                    # 环境变量（已注释）
│
├── docs/                   # 前端单页应用
│   └── index.html          # 全部前端代码（HTML + CSS + JS）
│
├── data/                   # 数据目录（volume 挂载）
│   ├── worldcup_2026_schedule.json
│   ├── teams.json
│   ├── recommendations.json
│   ├── odds_snapshot.json
│   ├── odds_movements.json
│   └── ...
│
└── Dockerfile
    docker-compose.yml
```

---

## 五、关键 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `GET /api/health` | GET | 健康检查 + DB 状态 + 同步状态 |
| `GET /api/matches` | GET | 合并数据（英超 + 世界杯） |
| `GET /api/epl/matches` | GET | 英超数据 |
| `GET /api/worldcup/matches` | GET | 世界杯数据 |
| `GET /api/matches/{league}/{source_id}` | GET | 比赛详情（含快照+分析） |
| `POST /api/auth/register` | POST | 用户注册 |
| `POST /api/auth/login` | POST | 用户登录 |
| `GET /api/auth/me` | GET | 当前用户信息 |
| `POST /api/admin/sync` | POST | 手动触发同步 |
| `GET /api/admin/sync-runs` | GET | 同步历史记录 |

---

## 六、已知限制

1. **The Odds API** 只对近几天比赛返回赔率，历史比赛无赔率数据
2. **API-Football** 赔率数据已配置但未充分利用（球队名匹配问题）
3. **竞彩数据** 尚未接入（需真实竞彩赔率 CSV）
4. **AI 分析** 需要 OPENAI_API_KEY，当前为空
5. **前端** 是纯 HTML+JS 单页，无框架（Vue/React），适合小改动但不适合复杂交互

---

## 七、测试与部署

```bash
# 开发测试
cd /vol1/1000/github/worldcup-bet-2026
docker compose up -d --build

# 快速更新前端（无需重建）
docker cp docs/index.html worldcup-bet-2026:/app/docs/index.html

# 查看日志
docker compose logs -f worldcup-ai

# 数据库检查
docker exec worldcup-bet-2026-db psql -U worldcup -d worldcup_ai -c "\dt"
```

---

## 八、Codex 建议下一步

1. **先修前端体验** — 详情面板的多来源赔率展示 + 指数历史简化
2. **再接赔率数据** — 优化 `fetch_api_football_odds`，提高赔率覆盖率
3. **然后做 AI 分析** — 接入 OPENAI_API_KEY，启用分析管道
4. **最后做用户功能** — 个人中心、收藏、投注记录等

---

*交接完毕。祝开发顺利！⚽*
