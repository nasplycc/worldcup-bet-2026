# 🔧 Codex 前端修复任务 — 赔率展示 & 用户体验优化

> 创建时间：2026-05-17 | 优先级：🔴 高

---

## 一、当前环境

- **项目路径**：`/vol1/1000/github/worldcup-bet-2026`
- **Docker 部署**：`docker compose up -d` 运行中
- **访问地址**：`NAS_IP:8088`（frp 映射到 `https://foot.nasply.site`）
- **前端文件**：`docs/index.html`（单页应用，纯 HTML + JS，无框架）
- **后端**：Flask `server.py`，PostgreSQL 数据库
- **Git 仓库**：`https://github.com/nasplycc/worldcup-bet-2026`（main 分支）

---

## 二、核心问题

### 🔴 问题 1：赔率数据覆盖率太低，页面看起来"没有赔率"

**现状：**
- 380 场英超只有 **5 场有赔率**（当天比赛，2026-05-17）
- 104 场世界杯只有 **12 场有赔率**
- 总计 484 场比赛只有 **17 场（3.5%）** 显示赔率
- 其余全部显示 "AI分析待接入" / "竞彩赔率待开售" / "待评估"

**原因：**
- The Odds API 只对近期/当天比赛返回赔率
- 英超数据是 2025-08-16 到 2026-05-24 的完整赛季，历史比赛没有实时赔率
- `attach_odds()` 函数用 `match_date_key()` 精确匹配日期，日期对不上就匹配不到

**需要 Codex 做的事：**
1. 前端默认按"近 7 天"或"近 3 天"过滤显示，不要一次性展示 484 场
2. 增加数据状态提示（"赔率未开售"、"暂无数据"等），不要只显示"待评估"
3. 有赔率的比赛要高亮显示（加颜色/标签），让用户一眼看到
4. 增加联赛切换时，只显示该联赛有赔率/近期比赛

### 🟡 问题 2：英超球队中文名映射不完整

**现状：**
- EPL 比赛 `homeName` 和 `awayName` 字段为空
- 前端用 `homeFull`（如 "Manchester United FC"）fallback
- 前端 `teamAliases` / `nameMap` 可能没有覆盖所有英超球队中文名
- 用户看到英文队名，体验不好

**需要 Codex 做的事：**
1. 完善前端 `teamAliases` / `nameMap` 中英队名映射表（至少覆盖 20 支英超球队）
2. 或者在后端 EPL 数据组装时补上中文名

### 🟡 问题 3：前端默认显示 484 场比赛，信息过载

**现状：**
- `loadMatches()` 一次性加载全部 484 场
- 英超 380 场（大部分已结束）+ 世界杯 104 场
- 用户打开页面要滚动很久，且大部分比赛没有赔率/AI 分析

**需要 Codex 做的事：**
1. 默认只显示"未来 7 天"或"近 3 天"的比赛
2. 增加日期过滤器
3. 增加"有赔率"/"无赔率"筛选
4. 有赔率的比赛自动排到最前面

---

## 三、API 数据接口参考

### GET /api/matches

返回合并数据（英超 + 世界杯）：

```json
{
  "count": 484,
  "matches": [
    {
      "id": "12345",
      "league": "英超",
      "competition": "Premier League",
      "date": "2026-05-17",
      "time": "22:00",
      "home": "MUN",
      "homeName": "",
      "homeFull": "Manchester United FC",
      "away": "NOT",
      "awayName": "",
      "awayFull": "Nottingham Forest FC",
      "status": "upcoming",
      "oddsAvailable": true,
      "odds": {
        "source": "the-odds-api",
        "bookmaker": "Pinnacle",
        "updated": "2026-05-17T03:08:57Z",
        "h2h": {"home": 1.63, "draw": 4.46, "away": 5.03},
        "totals": [{"name": "Over", "point": 3.0, "price": 1.94}]
      },
      "pick": {
        "type": "odds",
        "label": "H 1.63 / D 4.46 / A 5.03",
        "conf": 0.0,
        "reason": "指数已接入，等待 AI 复评"
      }
    }
  ],
  "sources": {
    "epl": {"status": 200, "count": 380, "source": "football-data.org"},
    "worldcup": {"status": 200, "count": 104, "source": "database"}
  }
}
```

### 关键数据字段说明

| 字段 | 说明 | 有赔率时 | 无赔率时 |
|------|------|----------|----------|
| `oddsAvailable` | 是否有赔率 | `true` | `false` |
| `odds` | 赔率详情 | 有 h2h/totals | 无 |
| `pick.label` | 推荐标签 | "H 1.63 / D 4.46 / A 5.03" | "AI分析待接入" |
| `pick.type` | 推荐类型 | "odds" | "pending" |
| `homeName` | 主队中文名 | 空 | 空 |
| `homeFull` | 主队全名 | "Manchester United FC" | "Liverpool FC" |

---

## 四、前端代码结构

### 入口：`docs/index.html`

纯单页应用，无框架。结构：
- `<style>` — 内联 CSS
- `<body>` — 导航栏 + 联赛筛选 + 状态筛选 + 搜索框 + 赛事列表
- `<script>` — JS 逻辑（约 400 行）

### 关键 JS 函数

```javascript
// 数据加载
loadMatches()          // 从 api/matches 获取数据
attachOdds()           // （后端已做）

// 渲染
render()               // 主渲染入口
card(m)                // 单场比赛卡片渲染
pickLabel(m)           // 推荐标签
pickDesc(m)            // 推荐描述
conf(m)                // 信心百分比

// 筛选
filtered()             // 按联赛/状态/搜索过滤
setStatus(value)       // 状态筛选
setLeague(value)       // 联赛筛选

// 工具
home(m)                // 主队名 zhName(m.homeName||m.homeFull||m.home)
away(m)                // 客队名 zhName(m.awayName||m.awayFull||m.away)
zhName(value)          // 中文名映射
teamAliases            // 队名别名表
nameMap                // 队名映射表
statusName(status)     // 状态文字
flag(code)             // 国旗 emoji
```

---

## 五、修改要求

### 5.1 赔率高亮 & 智能排序（必须做）

1. **有赔率的比赛排在最前面**，无论联赛/日期
2. **赔率卡片加视觉区分**：
   - 加 "💰 有赔率" 标签或边框颜色变化
   - 赔率数字用颜色区分（主胜绿色/平局黄色/客胜蓝色）
3. 无赔率的比赛显示明确状态：
   - "📅 赔率未开售"（未来比赛但无赔率）
   - "✅ 已结束"（历史比赛）
   - "⏳ AI 待分析"（未来比赛有赔率但未分析）

### 5.2 默认显示优化（必须做）

1. 默认只显示 **未来 7 天** 的比赛
2. 增加"显示全部"开关
3. 有赔率的比赛数量在导航栏显示（如 "💰 17 场有赔率"）

### 5.3 球队中文名映射（建议做）

完善 `teamAliases` 对象，覆盖至少英超 20 队：

```javascript
const teamAliases = {
  'Manchester United FC': '曼联',
  'Manchester City FC': '曼城',
  'Liverpool FC': '利物浦',
  'Chelsea FC': '切尔西',
  'Arsenal FC': '阿森纳',
  'Tottenham Hotspur FC': '热刺',
  'Newcastle United FC': '纽卡斯尔',
  'Aston Villa FC': '阿斯顿维拉',
  'West Ham United FC': '西汉姆联',
  'Brighton & Hove Albion FC': '布莱顿',
  'Crystal Palace FC': '水晶宫',
  'Everton FC': '埃弗顿',
  'Nottingham Forest FC': '诺丁汉森林',
  'Brentford FC': '布伦特福德',
  'Fulham FC': '富勒姆',
  'Wolverhampton Wanderers FC': '狼队',
  'AFC Bournemouth': '伯恩茅斯',
  'Leeds United FC': '利兹联',
  'Sunderland AFC': '桑德兰',
  'Ipswich Town FC': '伊普斯维奇',
  'Leicester City FC': '莱斯特城',
  'Southampton FC': '南安普顿',
  // 世界杯球队
  'Mexico': '墨西哥', 'South Africa': '南非', 'USA': '美国',
  'Brazil': '巴西', 'Argentina': '阿根廷', 'Germany': '德国',
  'France': '法国', 'England': '英格兰', 'Spain': '西班牙',
  'Japan': '日本', 'Australia': '澳大利亚', 'Morocco': '摩洛哥',
  // ... 补充更多
};
```

### 5.4 响应式体验（建议做）

1. 比赛卡片在小屏幕上布局优化
2. 赔率数字在移动端可读性

---

## 六、测试方式

修改 `docs/index.html` 后：

```bash
cd /vol1/1000/github/worldcup-bet-2026

# 方式 1：直接重建容器
docker compose up -d --build

# 方式 2：只复制前端文件（更快）
docker cp docs/index.html worldcup-bet-2026:/app/docs/index.html

# 验证
curl -s http://127.0.0.1:8088/ | head -5   # 确认前端已更新
```

---

## 七、当前数据快照（方便调试）

### 有赔率的比赛（17 场）

**英超（5 场，2026-05-17）：**
| 主队 | 客队 | 主胜 | 平局 | 客胜 |
|------|------|------|------|------|
| 曼联 | 诺丁汉森林 | 1.63 | 4.46 | 5.03 |
| 布伦特福德 | 水晶宫 | 1.68 | 4.36 | 4.73 |
| 埃弗顿 | 桑德兰 | 1.85 | 3.87 | 4.20 |
| 利兹联 | 布莱顿 | 3.40 | 3.71 | 2.13 |
| 狼队 | 富勒姆 | 3.90 | 3.86 | 1.93 |

**世界杯（12 场，2026-06-13 至 06-23）：**
| 主队 | 客队 | 日期 | 主胜 | 平局 | 客胜 |
|------|------|------|------|------|------|
| 美国 | 巴拉圭 | 06-13 | 2.04 | 3.62 | 3.76 |
| 海地 | 苏格兰 | 06-14 | 7.40 | 5.05 | 1.43 |
| 瑞典 | 突尼斯 | 06-15 | 1.95 | 3.54 | 4.25 |
| 阿根廷 | 阿尔及利亚 | 06-17 | 1.42 | 4.50 | 9.50 |
| 奥地利 | 约旦 | 06-17 | 1.29 | 5.30 | 9.50 |
| 乌兹别克斯坦 | 哥伦比亚 | 06-18 | 8.50 | 4.55 | 1.44 |
| 巴西 | 海地 | 06-20 | - | - | - |
| 厄瓜多尔 | 库拉索 | 06-21 | 1.22 | 6.00 | 12.00 |
| 突尼斯 | 日本 | 06-21 | 4.60 | 3.30 | 1.83 |
| 新西兰 | 埃及 | 06-22 | 4.80 | 3.50 | 1.73 |
| 挪威 | 塞内加尔 | 06-23 | 2.10 | 3.62 | 3.62 |
| 约旦 | 阿尔及利亚 | 06-23 | 5.15 | 3.82 | 1.75 |

---

## 八、后端 API 已就绪

以下内容已经 OK，**不需要改后端**：

- ✅ `/api/matches` — 合并数据接口，返回 484 场
- ✅ `/api/epl/matches` — 英超数据（含赔率）
- ✅ `/api/worldcup/matches` — 世界杯数据（含赔率）
- ✅ `/api/health` — 健康检查
- ✅ `/api/auth/login` — 登录
- ✅ `/api/auth/register` — 注册
- ✅ `/api/auth/me` — 用户信息
- ✅ `/api/admin/sync` — 手动触发同步
- ✅ `/api/admin/sync-runs` — 同步历史

**The Odds API Key 已配置**，每天自动同步赔率数据。

---

*交接完毕。祝开发顺利！⚽*
