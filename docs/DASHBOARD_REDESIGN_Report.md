# Dashboard 重新設計報告

> 報告日期：2026-04-16
> 基於：ui-ux-pro-max Executive Dashboard + Data-Dense Dashboard 風格
> 主題色：AgentK Teal (#1A9AAA) + Orange (#F5A623)

---

## 一、現狀問題

| 問題 | 說明 |
|------|------|
| KPI 卡牌無趨勢指標 | 只有數字，沒有跟前期比較的↑↓箭頭或 sparkline |
| 本月 Token 顯示 10035K | 這是全部累計而非本月（monthly 聚合需觸發 rebuild） |
| 排程任務顯示 `—` | 未對接 /api/schedules |
| 圖表佔據過多空間 | Token 趨勢 + Skill Top 5 兩個大圖，右側只有兩條最近活動 |
| 最近活動是假資料 | 只顯示「系統啟動完成」和「Skills 掃描完成」 |
| 沒有 Quick Actions | 管理員沒有快速入口做常用操作 |
| 缺少系統健康指標 | 沒有 uptime / 記憶體 / CPU / 錯誤率 |

---

## 二、建議重新設計方案

### 2.1 佈局結構

```
┌─────────────────────────────────────────────────────────────┐
│ Dashboard                                          日/周/月  │
├───────────┬───────────┬───────────┬───────────┬─────────────┤
│ Skills    │ Workflows │ 本月Token │ 排程任務    │ 今日 Calls   │
│    14     │    19     │  707K     │  2 active  │    23       │
│ ↑2 本周新增│ ↓1 vs上周  │ ↑12%     │ 5 paused   │ ↑8 vs昨日   │
│ [sparkline] [sparkline] [sparkline] [sparkline]  [sparkline] │
├───────────┴───────────┴───────────┴───────────┴─────────────┤
│                                                              │
│  Token 用量趨勢（動態區間）              │  系統快訊           │
│  ┌─────────────────────────────────┐    │  ● Server 正常運行  │
│  │  [Area Chart]                    │    │  ● LINE Bot 連線中  │
│  │  Total Tokens + Skill Calls      │    │  ⚠ Log 檔案 58MB   │
│  └─────────────────────────────────┘    │  ● 上次部署 2hr ago │
│                                          │                    │
│  ┌──────────────┬──────────────────┐    │  最近活動           │
│  │ Skill Top 5  │ 模型用量分佈      │    │  ● 范書愷 修改技能  │
│  │ [H-Bar]      │ [Donut]          │    │  ● Workflow 執行完成│
│  └──────────────┴──────────────────┘    │  ● 排程推送成功     │
│                                          │  ● 新員工加入       │
├──────────────────────────────────────────┴────────────────────┤
│  Quick Actions                                                │
│  [Skills 管理] [Workflows] [Token 用量] [排程監控] [使用者]     │
└───────────────────────────────────────────────────────────────┘
```

### 2.2 KPI 卡牌升級

**現狀**：純數字 + 副標題

**建議改為**（Executive Dashboard 風格）：

```
┌──────────────────────┐
│ Skills 啟用數          │
│ ██████████████  14    │  ← 數字放大到 --font-2xl
│ ▲ +2 本週新增          │  ← 綠色↑ 或 紅色↓ 趨勢指標
│ ┈┈┈╱╲┈╱╲╱╲┈┈       │  ← 7 日 sparkline (inline SVG)
└──────────────────────┘
```

每張 KPI 卡需要：
- 數字（大字）
- 趨勢指標（↑↓ + 百分比或絕對值 + 綠/紅色）
- 7 日 Mini Sparkline（寬 100% 高 32px 的迷你折線圖）
- 底部 accent 色條改為頂部

### 2.3 圖表區域

| 圖表 | 改進 |
|------|------|
| Token 趨勢 | ✅ 已有動態區間，保持 |
| Skill Top 5 | 縮小為左半，加上呼叫次數 badge |
| **新增：模型用量分佈** | 右半 Donut Chart（GPT-4o / Gemini / Claude 佔比）|
| **新增：系統快訊** | 右側面板，取代靜態「最近活動」|

### 2.4 系統快訊面板（取代最近活動）

| 指標 | 來源 | 狀態顯示 |
|------|------|---------|
| Server 運行 | uptime | 🟢 正常 / 🔴 異常 |
| LINE Bot | webhook 狀態 | 🟢 連線中 / 🟡 延遲 |
| Log 檔案大小 | uma_server.log stat | ⚠️ 超過 50MB 時警告 |
| 最近部署 | git log --oneline -1 | 時間戳 |
| Skills 掃描 | 啟動時記錄 | 數量 + 時間 |

### 2.5 Quick Actions 列

底部一排快速跳轉按鈕：

```
[⚙️ Skills 管理] [🔀 Workflows] [📊 Token 用量] [⏱ 排程監控] [👥 使用者]
```

每個按鈕 → `location.hash = '#/skills'` 等

### 2.6 最近活動 Feed（真實資料）

從以下來源聚合：
- `token_usage.jsonl` 最後 10 筆 → 「范書愷 呼叫 mcp-web-search」
- Agent_skills git log 最後 5 筆 → 「[Skill Mgmt] Updated skill xxx」
- `workspace/schedules/*.json` → 「排程推送：每日新聞摘要」

---

## 三、配色方案

延用 AgentK 主題 + Productivity Tool #16 配色：

| 用途 | 色碼 | CSS 變數 |
|------|------|---------|
| KPI accent（Skills） | #1A9AAA | `--kway-blue` |
| KPI accent（Workflows）| #F5A623 | `--kway-orange` |
| KPI accent（Token） | #059669 | `--color-success` |
| KPI accent（排程） | #4285F4 | `--color-info` |
| KPI accent（Calls） | #8B5CF6 | 新增 `--color-purple` |
| 趨勢 ↑ | #059669 | `--color-success` |
| 趨勢 ↓ | #DC2626 | `--color-error` |
| Sparkline | #1A9AAA 20% opacity | |
| 快訊 正常 | #059669 | `--color-success` |
| 快訊 警告 | #D97706 | `--color-warning` |

---

## 四、技術實作

| 項目 | 方式 |
|------|------|
| Sparkline | Chart.js `type: "line"` 最小化（無 axis/legend/tooltip），或純 SVG path |
| 趨勢計算 | 比較本週 vs 上週（或本月 vs 上月）的 sum |
| 模型用量 | 從 `token_usage.jsonl` 最後 N 筆統計 `model` 欄位 |
| 系統快訊 | 新增 `GET /api/system/health` API（log size, uptime, skill count） |
| 最近活動 | 新增 `GET /api/activity-feed` API（聚合 token_usage + git log + schedules）|
| Quick Actions | 純 CSS 按鈕列，onclick → hash routing |

---

## 五、KPI 5 張 vs 4 張

**建議 5 張**（多一張「今日 Calls」）：

| # | 標題 | 數據來源 | 趨勢比較 |
|---|------|---------|---------|
| 1 | Skills 啟用數 | `/skills/list` count | 本週新增 vs 上週 |
| 2 | Workflow 總數 | `/api/workflows` count | 本週新增 vs 上週 |
| 3 | 本月 Token | `monthly[YYYY-MM].total_tokens` | vs 上月同期 |
| 4 | 排程任務 | `/api/schedules` active count | active / total |
| 5 | 今日 Calls | `daily[today].skill_calls + chat_calls` | vs 昨日 |

---

## 六、實作優先級

| 步驟 | 內容 | 預估工作量 |
|------|------|----------|
| 1 | KPI 5 張 + 趨勢指標（↑↓ + 百分比）| 中 |
| 2 | Quick Actions 按鈕列 | 小 |
| 3 | 圖表區改為 2×2 網格（趨勢 + Skill Top5 + Donut + 快訊）| 中 |
| 4 | 系統快訊面板 + `/api/system/health` | 中 |
| 5 | 最近活動真實資料 + `/api/activity-feed` | 大 |
| 6 | Mini Sparkline in KPI 卡 | 小 |
