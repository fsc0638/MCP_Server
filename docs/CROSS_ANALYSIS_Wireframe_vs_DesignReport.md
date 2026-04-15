# Wireframe × 設計報告 交叉分析

> 分析日期：2026-04-15
> 文件 A：AgentK UX Wireframe 設計稿（圖像）
> 文件 B：MCP_Server 管理後台 UI 設計報告（PDF, 8 頁）
> 分析人：范書愷 / Claude

---

## 一、文件結構對照

| Wireframe 區域 | PDF 章節 | 對應關係 |
|---------------|---------|---------|
| 頂部：流程圖 Welcome→Login→Role→Main | 第一章：現有架構盤點 | 直接對應 |
| 第二排：Dashboard V1-V4 迭代 | 4.1 Dashboard 總覽 | 直接對應 |
| 中間排左：Skill 詳細檢視 | 4.2 Skills 管理 | 直接對應 |
| 中間排中（藍框）：三欄管理頁 | 4.3 Workflows / 第四章整體架構 | **需釐清** |
| 中間排右：設定頁 / Playground | 第六章技術建議 / 未涵蓋 | 部分對應 |
| 下半部：Workflow Designer 6 張狀態圖 | 4.3 排程監控 + Workflow | 部分對應 |
| 左下：綠色監控面板 | 4.4 Token 用量 / 4.3 排程 | 概念對應 |

---

## 二、逐項交叉分析

### WF-01：使用者流程（登入→角色→主頁）

| 項目 | Wireframe | PDF 規範 | 狀態 | 問題 |
|------|-----------|---------|------|------|
| 登入頁 | Welcome→Login 節點 | 第一章：login.html Google/LINE OAuth | ✅ 一致 | — |
| 角色判斷 | Role Determination 菱形節點 | 第四章：admin.html 檢查 user role | ⚠️ 差異 | Wireframe 在登入後判斷；PDF 在進入 admin 時判斷 |
| 主頁導向 | 箭頭指向 Main Page | 第七章：Phase 1 admin.html | ⚠️ 差異 | Wireframe 導向單一 Main Page；PDF 分 chat.html（一般用戶）+ admin.html（管理員） |
| 功能清單 | 左側列出 5 項功能 | 第四章 Sidebar 7 個子頁面 | ❌ 不一致 | Wireframe 5 項 vs PDF 7 項（PDF 多了 Token 用量、系統設定） |

**建議**：以 PDF 的 7 個子頁面為準。Wireframe 的 5 項是早期版本，PDF 已擴充。

---

### WF-02：Dashboard 總覽（Wireframe 第二排 V1-V4）

| 項目 | Wireframe V4（最終版） | PDF 4.1 規範 | 狀態 | 問題 |
|------|---------------------|-------------|------|------|
| 佈局 | 三欄：左導航 + 中間工作區 + 右面板 | Sidebar 左 + Main Content 右 | ⚠️ 差異 | Wireframe 有右側面板；PDF 只有左 Sidebar + 右 Content |
| KPI 卡牌 | 中間區顯示 Task 卡牌（非 KPI） | 4 張 KPI Cards：Skills 啟用數 / Workflow 執行中 / 本月 Token / 排程任務 | ❌ 矛盾 | Wireframe 的 Task cards 是待辦事項；PDF 的 KPI Cards 是統計指標 |
| 圖表 | 無 | Token 趨勢 Area Chart + Skill Top 5 Bar + 模型用量 Donut | ❌ 遺漏 | Wireframe 完全沒有圖表區域 |
| 活動 Feed | 無 | 右側最近活動 feed（skill 修改/workflow 執行/排程觸發） | ❌ 遺漏 | Wireframe 未規劃 |
| 歡迎訊息 | "Welcome to AgentK" + Task 清單 | 未提及 | ⚠️ Wireframe 獨有 | PDF 未包含歡迎頁概念 |

**結論**：Wireframe 的 Dashboard 是**使用者工作台**（Task + 歡迎），PDF 的 Dashboard 是**管理員監控台**（KPI + 圖表）。兩者定位不同，應該**共存**：
- `chat.html` → 使用者工作台（對應 Wireframe V4）
- `admin.html #/dashboard` → 管理監控台（對應 PDF 4.1）

---

### WF-03：Skills 管理

| 項目 | Wireframe（中間排左） | PDF 4.2 規範 | 狀態 | 問題 |
|------|---------------------|-------------|------|------|
| 列表顯示 | 左側步驟清單 + 右側詳情 | 搜尋表 + Scope/Mode 篩選 + Actions 欄 | ⚠️ 差異 | Wireframe 是步驟檢視；PDF 是表格列表 |
| 編輯模式 | 綠色執行按鈕 | 右側 Drawer（250ms translateX 動畫） | ❌ 矛盾 | Wireframe 無 Drawer；PDF 明確要求 Drawer |
| 搜尋 | 無 | must_have: search + Ctrl+K 快速搜尋 | ❌ 遺漏 | Wireframe 未規劃全域搜尋 |
| Scope 篩選 | 無 | System/Department/Personal 篩選 | ❌ 遺漏 | Wireframe 未考慮三層分類 |
| Mode 指示 | 無 | executable(green), code(blue), semantic(grey) status dot | ❌ 遺漏 | — |
| Git Rollback | 無 | 版本記錄 + Git Rollback 功能 | ❌ 遺漏 | — |

**結論**：Wireframe 的 Skill 檢視是**早期概念**，PDF 規範遠更完整。**現有程式碼的 SkillEditor 已經比 Wireframe 更接近 PDF 規範**（有 scope 篩選、有 rollback）。但缺 Drawer 模式和 Ctrl+K 搜尋。

---

### WF-04：Workflow 管理（藍框 + 下半部）

| 項目 | Wireframe 藍框 | Wireframe 下半部 6 張 | PDF 4.3 | 狀態 |
|------|-------------|-------------------|---------|------|
| Landing 頁 | 三欄佈局：左列表 + 中卡牌 + 右屬性 | — | Scope 篩選 + 執行統計 | ⚠️ 差異 |
| 卡牌樣式 | 類似檔案管理器 | — | Card Grid + 狀態圖標 | ⚠️ 差異 |
| Canvas | — | 三欄：左 Skill 列表 + 中 Canvas + 右 Chat/Log | 未詳述 Canvas 細節 | Wireframe 更詳盡 |
| 執行狀態 | — | 5 種狀態：空白/編輯中/執行中/完成/失敗 | Status 語意色：green/amber/red | ✅ 一致 |
| 即時回饋 | — | 右側面板顯示日誌/步驟/錯誤 | 執行 Timeline | ⚠️ 差異 |

**結論**：
- **Landing 頁**：Wireframe 的藍框比 PDF 更具體（有三欄佈局），但 PDF 強調 Card Grid + 狀態色。**兩者互補**。
- **Canvas 設計**：**Wireframe 是唯一的詳盡參考**，PDF 幾乎沒涵蓋 Canvas 細節。
- **執行狀態**：Wireframe 的 5 張狀態圖是最佳參考，PDF 只提到顏色語意。

---

### WF-05：排程/監控（綠色面板 + PDF 4.3/4.4）

| 項目 | Wireframe 綠色面板 | PDF 4.3 + 4.4 | 狀態 |
|------|-----------------|--------------|------|
| Today's Tasks | 完成/進行中/待處理 | Card Grid + 狀態 | ✅ 一致 |
| System Status | CPU/Memory 狀態條 | 未提及 | ⚠️ Wireframe 獨有 |
| Token 圖表 | 無 | Stacked Area + Treemap + CSV 匯出 | ❌ Wireframe 遺漏 |
| 排程 Timeline | 無 | 最近執行記錄 Timeline | ❌ Wireframe 遺漏 |
| 時間範圍選擇 | 無 | 7d/30d/90d 切換 | ❌ Wireframe 遺漏 |

**結論**：Wireframe 的監控面板是**系統健康檢查**（CPU/Memory），PDF 的監控是**業務指標**（Token/排程）。兩者定位不同。

---

### WF-06：配色與風格

| 項目 | Wireframe | PDF 第五/六章 | 狀態 |
|------|-----------|------------|------|
| 主色調 | 白底 + 紅色標註文字 + 綠色/藍色 accent | Primary #1A9AAA + Accent #F5A623 | ⚠️ 差異 |
| 卡牌圓角 | 看起來 8-12px | 未明確指定（參考 plugin: 16-24px Bento Grid） | 需統一 |
| 狀態色 | 綠/黃/紅（從 Designer 狀態圖） | Success #059669 / Warning #D97706 / Destructive #DC2626 | ✅ 一致 |
| 字體 | 無標註 | Inter + Noto Sans TC | Wireframe 遺漏 |
| 動畫 | 無標註 | 150-300ms ease-out, prefers-reduced-motion | Wireframe 遺漏 |

---

### WF-07：技術架構

| 項目 | Wireframe | PDF 第六/七章 | 狀態 |
|------|-----------|------------|------|
| 頁面架構 | 暗示單一 Main Page（SPA 風格） | 新增 admin.html + hash routing | ❌ 矛盾 |
| 路由方式 | 未標示 | #/dashboard, #/skills, #/tokens | Wireframe 遺漏 |
| 框架 | 未標示 | Vanilla JS + HTML（無 build step） | — |
| 圖表 | 未標示 | Chart.js (CDN) | — |
| Z-Index | 未標示 | 10/20/30/50 層級規範 | — |

---

## 三、矛盾清單（必須決策）

| # | 矛盾點 | Wireframe 主張 | PDF 主張 | 建議 |
|---|--------|-------------|---------|------|
| C1 | Dashboard 定位 | 使用者工作台（Task cards） | 管理監控台（KPI + 圖表） | **共存**：chat.html 工作台 + admin.html 監控台 |
| C2 | Skill 編輯模式 | 全頁檢視 | 右側 Drawer（250ms 滑入） | **採 PDF**：Drawer 更符合管理後台 UX |
| C3 | 管理頁面數量 | 5 個功能 | 7 個子頁面 | **採 PDF**：更完整 |
| C4 | Workflow Landing | 三欄佈局（藍框） | Card Grid + 狀態色 | **合併**：用三欄佈局但卡牌採 PDF 狀態色 |
| C5 | 頁面架構 | 單一 Main Page | admin.html 獨立 | **採 PDF**：admin.html 獨立管理 |
| C6 | 監控面板 | 系統健康（CPU/Memory） | 業務指標（Token/排程） | **合併**：Dashboard 放業務指標，設定頁放系統健康 |

---

## 四、遺漏清單（兩邊都沒覆蓋）

| # | 功能 | 說明 | 影響 |
|---|------|------|------|
| G1 | LINE Bot 即時監控 | 無 Webhook 健康/回應時間/錯誤率的視覺化 | 營運盲區 |
| G2 | Workflow 版本管理 | 無 Workflow 歷史版本/diff/回滾 | 只有 Skill 有 rollback |
| G3 | 批量操作 | 無批量啟用/停用/刪除 Skill/Workflow | 管理效率 |
| G4 | 匯入/匯出 | 無 Skill/Workflow 的 JSON 匯入匯出 | 跨環境遷移 |
| G5 | 通知中心 | 無統一的通知管理（排程失敗/Skill 錯誤/Token 超限） | 只有 LINE 推送 |
| G6 | 多語系 | 設定頁有語言選擇但無 i18n 實作 | 國際化 |

---

## 五、現有程式碼 vs 兩份文件

| 功能 | Wireframe 要求 | PDF 要求 | 程式碼現狀 | 完成度 |
|------|-------------|---------|----------|--------|
| 登入/角色 | ✅ | ✅ | ✅ login.html + employee_lookup | 90% |
| 對話頁面 | ✅ V4 三欄 | 未涵蓋 | ✅ chat.html | 95% |
| Skill Editor | 早期概念 | Drawer + 表格 | ✅ 全頁編輯器 + scope | 70% |
| Workflow Landing | 藍框三欄 | Card Grid | ⚠️ 做了一半（佈局衝突） | 30% |
| Workflow Designer | 6 張狀態圖 | 未詳述 | ✅ FlowDesigner + 路由 | 80% |
| admin.html | 無 | 獨立管理頁 | ❌ 不存在 | 0% |
| KPI Dashboard | 無 | 4 KPI + 圖表 | ❌ 不存在 | 0% |
| Token Dashboard | 無 | Stacked Area + Treemap | ❌ 不存在 | 0% |
| 排程監控 | 綠色面板概念 | Card + Timeline | ❌ 不存在 | 0% |
| 使用者管理 | 無 | 員工列表 + Profile | ❌ 不存在 | 0% |

---

## 六、建議實作優先級

基於交叉分析，調整後的優先級：

```
Phase 0（修復）
└── 修好 Workflow Landing Page 佈局

Phase 1（admin.html 骨架 — PDF 第七章）
├── admin.html + Sidebar + hash routing
├── Dashboard KPI Cards（4 張）
└── Token 趨勢 Area Chart

Phase 2（Skills 管理升級 — PDF 4.2 + Wireframe 互補）
├── Skills 表格列表（取代現有全頁編輯器）
├── 右側 Drawer 編輯模式
├── Ctrl+K 全域搜尋
└── Mode status dot（executable/code/semantic）

Phase 3（Workflow 完善 — Wireframe 下半部 + PDF 4.3）
├── Landing Page 卡牌（含 PDF 狀態色）
├── Designer 執行狀態回饋（Wireframe 5 張狀態圖）
└── 執行 Timeline（PDF 4.3）

Phase 4（監控 — 綠色面板 + PDF 4.4）
├── Token Dashboard（圖表 + CSV 匯出）
├── 排程監控（Card Grid + Timeline）
└── 系統健康指標

Phase 5（管理 — PDF 第七章）
├── 使用者/權限管理
├── 系統設定頁
└── 通知中心
```
