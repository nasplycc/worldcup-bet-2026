# 🔧 Codex 前端优化任务 v2 — 筛选器简化 + 详情中文化

> 创建时间：2026-05-17 | 优先级：🔴 高

---

## 一、当前环境

- **项目路径**：`/vol1/1000/github/worldcup-bet-2026`
- **Docker 部署**：`docker compose up -d` 运行中
- **访问地址**：`NAS_IP:8088`
- **前端文件**：`docs/index.html`（单页应用，纯 HTML + JS，无框架）
- **后端 API**：`server.py`（Flask），详情接口 `GET /api/matches/{league}/{source_id}`
- **数据库**：PostgreSQL 16

---

## 二、任务 1：筛选器简化（必须做）

### 当前筛选器现状（太复杂）

当前页面有 **4 组筛选控件**，组合起来非常混乱：

| 控件 | 当前选项 | 问题 |
|------|----------|------|
| **联赛 Tab** | 世界杯 / 英超 | ✅ 保留，简单清晰 |
| **年份下拉** | 2026 | ⚠️ 目前只有一年，意义不大 |
| **日期范围** | 开始日期 → 结束日期 | ⚠️ 两个 input 太占空间，用户操作繁琐 |
| **状态 Tab** | 全部 / 未开始 / 进行中 / 已结束 | ⚠️ **核心问题所在** |
| **指数筛选** | 全部 / 有指数 / 无指数 | ⚠️ 和状态组合后逻辑混乱 |

### 问题诊断

**1. 状态筛选（未开始/进行中/已结束）存在的必要性评估：**

- **世界杯 104 场**：全部是 `upcoming`（未来比赛），没有 live/finished
- **英超 380 场**：99% 是 `finished`（历史比赛），只有今天 5 场 `upcoming`
- **结论**：状态筛选对世界杯完全无用（全是未开始），对英超只有区分"已结束历史"和"当天待赛"的意义

**2. 当前筛选逻辑的混乱：**

```javascript
// 当前 filtered() 逻辑
function filtered() {
  base = 联赛过滤 && 年份过滤 && 状态过滤 && 指数过滤 && 搜索
  narrowed = 日期范围过滤
  // 如果结果为空，自动 fallback 到"非已结束的前12场"
  if (!narrowed.length) return sortMatches(base.filter(m => m.status !== 'finished')).slice(0,12)
}
```

- 默认只显示有赔率或近7天比赛（`selectedDateMatch`）
- 如果用户选了"已结束" + "有指数"，可能直接空结果
- 空结果时自动 fallback 的逻辑让用户困惑

### 建议优化方案

**简化为 2 组筛选：**

| 控件 | 选项 | 说明 |
|------|------|------|
| **联赛 Tab** | 世界杯 / 英超 / 全部 | 保留 |
| **快捷按钮组** | 🔥 有指数 | 💰 有赔率的比赛（排最前）|
| | 📅 近 7 天 | 默认显示，时间窗口内所有比赛 |
| | ✅ 已结束 | 历史比赛记录 |
| | 🔍 全部 | 显示全部 |

**具体规则：**
1. **默认**：联赛 Tab + 📅 近 7 天（自动包含有指数的比赛）
2. 用户点击 🔥 有指数：只显示有赔率的比赛，不限日期
3. 用户点击 ✅ 已结束：只显示历史已结束比赛
4. 用户点击 🔍 全部：显示所有比赛
5. **删除**：年份下拉、状态 Tab、日期范围选择器
6. 搜索框保留

**排序逻辑：**
- 有赔率的比赛始终排最前
- 然后按日期升序
- 最后按联赛分组

---

## 三、任务 2：比赛详情页中文化（必须做）

### 当前详情页结构

点击比赛卡片后弹出的详情面板，包含 4 个模块：

```
┌─────────────────────────────────────┐
│  墨西哥 vs 南非                      │  ← 标题（已中文）
│  世界杯 · 2026-06-12 05:00           │  ← 副标题（已中文）
├─────────────────────────────────────┤
│  【基础信息】                        │
│    阶段: 小组赛 · A组                │
│    状态: 未开始                      │
│    球场: Estadio Azteca             │
│    城市: 墨西哥城                    │
│    数据源: database                  │  ← ⚠️ 应该显示"数据库"
├─────────────────────────────────────┤
│  【指数信息】                        │
│    机构: Pinnacle                    │
│    更新时间: 2026-05-17T03:08:57Z   │  ← ⚠️ 时间格式要本地化
│    主胜: 1.63  平局: 4.46  客胜: 5.03│
├─────────────────────────────────────┤
│  【AI分析】                          │
│    待评估 / AI分析待接入             │
├─────────────────────────────────────┤
│  【指数快照】                        │
│    h2h · home · Pinnacle · 1.63     │  ← ⚠️ 全是英文！
│    h2h · draw · Pinnacle · 4.46     │
│    totals · Over · Pinnacle · 1.94  │
└─────────────────────────────────────┘
```

### 需要中文化的部分

**1. 数据源显示（`source` 字段）**
- `"database"` → "数据库"
- `"football-data.org"` → "football-data.org"（保留）
- `"the-odds-api"` → "The Odds API"
- `"api-football"` → "API-Football"
- `"openfootball"` → "OpenFootball"

**2. 指数快照（`snapshotsHtml` 函数）**

当前快照列表显示的是原始字段值，全是英文：

```
h2h · home · Pinnacle · 1.63
h2h · draw · Pinnacle · 4.46
totals · Over · Pinnacle · 1.94
```

需要改为中文：

```
胜平负 · 主胜 · Pinnacle · 1.63
胜平负 · 平局 · Pinnacle · 4.46
大小球 · 大球 · Pinnacle · 1.94
```

**字段映射表：**

| 原始值 | 中文显示 |
|--------|----------|
| `h2h` | "胜平负" |
| `totals` | "大小球" |
| `home` | "主胜" |
| `away` | "客胜" |
| `draw` | "平局" |
| `Over` | "大球" |
| `Under` | "小球" |

**3. 时间格式本地化**

`updatedAt` 字段显示 `2026-05-17T03:08:57Z`，改为：
- `2026-05-17 11:08`（北京时间 24h 格式）

**4. 其他英文字段**

在 `detail-box` 中所有可能出现的英文字段：

| 原始值 | 中文 |
|--------|------|
| `Group Stage` | "小组赛" |
| `Round of 16` | "16强" |
| `Quarter-finals` | "8强" |
| `Semi-finals` | "半决赛" |
| `Final` | "决赛" |
| `upcoming` | "未开始" |
| `live` / `ht` | "进行中" |
| `finished` | "已结束" |
| `postponed` | "延期" |
| `cancelled` | "取消" |
| `A组` ~ `H组` | 保持"A组"~"H组" |

### 5. `indexInfo` 标签名优化

当前中文模式下 "指数信息" 这个标签有点生硬，建议：
- `indexInfo` → "实时指数" 或 "赔率信息"
- `historyInfo` → "指数历史" 或 "赔率快照"
- `basicInfo` → "比赛信息"

---

## 四、技术要点

### 4.1 前端代码结构

```javascript
// 当前筛选相关函数
selectedStatusMatch(m)    // 状态过滤
selectedOddsMatch(m)      // 指数过滤
selectedDateMatch(m)      // 日期过滤
filtered()                // 综合过滤

// 需要重写为简化版
selectedQuickFilter(m)    // 单一快捷按钮过滤
filtered()                // 简化：联赛 + 快捷 + 搜索

// 详情相关函数
renderDetail(m, data)     // 渲染详情面板
currentOddsBox(m)         // 赔率信息框
snapshotsHtml(items)      // 指数快照列表 ← 需要中文化
analysisHtml(items, m)    // AI分析展示
```

### 4.2 后端详情 API

```
GET /api/matches/{league_key}/{source_id}

返回示例：
{
  "match": { ... },           // 比赛数据
  "oddsSnapshots": [...],     // 赔率快照（需中文化）
  "analysisResults": [...]    // AI分析结果
}
```

快照数据结构：
```json
{
  "market": "h2h",        // → "胜平负"
  "selection": "home",    // → "主胜"
  "bookmaker": "Pinnacle",
  "price": 1.63,
  "capturedAt": "2026-05-17T03:08:57Z"  // → "2026-05-17 11:08"
}
```

### 4.3 当前页面 HTML 结构

```html
<!-- 联赛 Tab -->
<div class="league-tabs" id="leagueTabs">
  <button class="tab active" data-league="worldcup">世界杯</button>
  <button class="tab" data-league="epl">英超</button>
</div>

<!-- 当前子筛选区（需要简化） -->
<div class="sub-filters">
  <select class="year-select">...</select>     <!-- 删除 -->
  <label><span>开始</span><input type="date"></label>  <!-- 删除 -->
  <label><span>结束</span><input type="date"></label>  <!-- 删除 -->
</div>

<!-- 状态 Tab（需要删除） -->
<div class="status-tabs" id="statusTabs">
  <button data-status="all">全部</button>
  <button data-status="upcoming">未开始</button>
  <button data-status="live">进行中</button>
  <button data-status="finished">已结束</button>
</div>
```

**替换为快捷按钮组：**
```html
<div class="quick-filters" id="quickFilters">
  <button class="quick-btn active" data-filter="recent7">📅 近 7 天</button>
  <button class="quick-btn" data-filter="with-odds">🔥 有指数</button>
  <button class="quick-btn" data-filter="finished">✅ 已结束</button>
  <button class="quick-btn" data-filter="all">🔍 全部</button>
</div>
```

---

## 五、测试方式

```bash
cd /vol1/1000/github/worldcup-bet-2026

# 方式 1：快速替换前端文件（推荐用于调试）
docker cp docs/index.html worldcup-bet-2026:/app/docs/index.html

# 方式 2：重建容器
docker compose up -d --build

# 验证
curl -s http://127.0.0.1:8088/ | head -5
```

---

## 六、验收标准

### 任务 1：筛选器简化
- [ ] 页面无年份下拉、无状态 Tab、无日期范围选择器
- [ ] 新增快捷按钮组（近 7 天 / 有指数 / 已结束 / 全部）
- [ ] 默认显示近 7 天比赛
- [ ] 有指数的比赛自动排在最前
- [ ] 筛选组合不会产生空结果

### 任务 2：详情中文化
- [ ] 快照列表显示中文（胜平负/大小球/主胜/平局/客胜等）
- [ ] 时间格式本地化为北京时间
- [ ] 数据源名称中文化
- [ ] 模块标题优化（"比赛信息"/"实时指数"/"指数历史"）
- [ ] 所有英文字段在中文模式下有对应翻译

---

*交接完毕。祝开发顺利！⚽*
