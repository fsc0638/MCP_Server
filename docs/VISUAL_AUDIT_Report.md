# AgentK 視覺設計審計報告

> 審計日期：2026-04-15
> 審計範圍：所有 CSS 檔案 + JS 內嵌樣式
> 審計方式：逐行掃描 style.css / chat.css / workflow.css / admin.css / settings.css / login.css / admin.js / workflow.js

---

## 一、色系問題

### 1.1 未定義的 CSS 變數（正在使用但 `:root` 中不存在）

| 變數 | 使用檔案 | 使用次數 | 影響 |
|------|---------|---------|------|
| `--shadow-clay-sm` | chat.css | 12+ 行 | 渲染為空值（無陰影） |
| `--shadow-clay-md` | chat.css | 5+ 行 | 渲染為空值 |
| `--bg-main` | admin.css, workflow.css | 7+ 行 | 依賴 fallback #F8F9FB |
| `--text-tertiary` | 全部 CSS 檔 | 50+ 行 | 依賴 fallback #94A3B8 |
| `--accent-teal` | chat.css | 6 行 | 未定義 |
| `--accent-teal-pale` | chat.css | 7 行 | 未定義 |

### 1.2 硬編碼顏色（未使用 CSS 變數）

**chat.css — 最嚴重：**

| 問題 | 出現次數 | 範例 |
|------|---------|------|
| 白色混用 `#fff` 和 `#FFFFFF` | 15 處 | 應統一為 `#fff` |
| 藍色焦點 rgba 三種不同透明度 | 3 種 | `0.10` / `0.15` / `0.35` |
| 黑色邊框 rgba 四種不同透明度 | 4 種 | `0.06` / `0.07` / `0.08` / `0.09` |
| 硬編碼紅色 `#dc2626` | 多處 | 應定義 `--color-error` |
| 硬編碼綠色 `#1A7A55` / `#059669` | 多處 | 應定義 `--color-success` |

**workflow.js — Block 顏色全部硬編碼在 JS：**
```javascript
"start": { color: "#34a853" }      // 綠色
"end":   { color: "#ea4335" }      // 紅色
"branch":{ color: "#00897b" }      // 青色
// ... 10+ 個 skill 顏色
```

**admin.js — 60+ 處 inline style 硬編碼顏色：**
- `background:#059669` / `#1A9AAA` / `#d97706` / `#ea4335`
- `color:#dc2626` / `#92400e`
- 全部在 `innerHTML` 模板字串中

### 1.3 狀態色未統一定義

| 語意 | 使用的色碼 | 出現的變體 |
|------|----------|----------|
| 成功/啟用 | `#059669`, `#34a853`, `#1a7a43` | 3 種綠色 |
| 錯誤/刪除 | `#ea4335`, `#dc2626`, `#f87171` | 3 種紅色 |
| 警告/暫停 | `#f5a623`, `#d97706`, `#fbbf24` | 3 種橘色 |
| 資訊/連結 | `#4285f4`, `#3b82f6`, `#1565c0` | 3 種藍色 |

### 1.4 Modal 遮罩透明度不一致

| 元件 | 透明度 | 檔案 |
|------|--------|------|
| Drawer overlay | 0.30 | admin.css |
| Ctrl+K overlay | 0.40 | admin.css |
| 刪除確認 overlay | 0.45 | workflow.css |
| 儲存狀態 overlay | 0.35 | workflow.css |

**應統一為一個值**（建議 `0.40`）

---

## 二、字體問題

### 2.1 字型（font-family）

| 檔案 | 定義 | 問題 |
|------|------|------|
| style.css | `Inter, "Noto Sans TC", system-ui, sans-serif` | ✅ 正確 |
| admin.css | 未宣告（繼承 style.css） | ✅ 但依賴繼承 |
| workflow.css | 未宣告 | ✅ 繼承 |
| admin.js | `font-family:monospace`（多處內嵌） | ⚠️ 應用 CSS class |

### 2.2 字體大小（font-size）— 無標準化比例

**chat.css 使用 20+ 種不同 font-size：**
`0.65, 0.68, 0.70, 0.73, 0.76, 0.77, 0.78, 0.79, 0.80, 0.82, 0.83, 0.84, 0.85, 0.86, 0.90, 0.92, 0.95, 1.4, 1.75 rem`

**admin.css 使用 18+ 種：**
`0.55, 0.58, 0.60, 0.62, 0.63, 0.65, 0.66, 0.68, 0.70, 0.72, 0.75, 0.76, 0.78, 0.80, 0.82, 0.85, 0.90, 1.0, 1.1, 1.2, 1.6 rem`

**重疊問題：** `0.78rem` 和 `0.79rem` 同時存在（視覺上無法區分）

**建議標準化比例：**
```
--font-2xs: 0.60rem   (ID、次要標籤)
--font-xs:  0.68rem   (badge、提示文字)
--font-sm:  0.75rem   (表格內容、按鈕)
--font-base: 0.82rem  (一般文字)
--font-md:  0.90rem   (小標題)
--font-lg:  1.05rem   (頁面標題)
--font-xl:  1.20rem   (大標題)
--font-2xl: 1.60rem   (KPI 數字)
```

### 2.3 字重（font-weight）

使用的值：`400, 500, 600, 700, 800`
- `800`：只在 KPI 數字和 Logo 使用 → ✅ 合理
- `700`：標題、Drawer title → ✅ 合理
- `600`：按鈕、badge、表格名稱 → ✅ 合理
- `500`：Sidebar nav、一般文字 → ✅ 合理
- `400`：正文 → ✅ 合理

**結論：字重使用合理，無需調整**

---

## 三、圓角（border-radius）問題

### 3.1 已定義的變數 vs 實際使用

| 已定義 | 值 | 實際使用情況 |
|--------|-----|------------|
| `--radius-xs` | 4px | ✅ 少量使用 |
| `--radius-sm` | 6px | ⚠️ 大量硬編碼為 `6px` 而非引用變數 |
| `--radius-md` | 10px | ⚠️ 很少被引用 |
| `--radius-lg` | 16px | ⚠️ 幾乎不被使用 |
| `--radius-xl` | 22px | ❌ 未被使用 |

**問題：大量使用 `8px` 和 `12px`（不在定義範圍內）**

| 硬編碼值 | 出現次數 | 應該是 |
|---------|---------|--------|
| `6px` | 20+ 處 | `var(--radius-sm)` |
| `8px` | 30+ 處 | **未定義**（介於 sm 和 md 之間） |
| `10px` | 10+ 處 | `var(--radius-md)` |
| `12px` | 15+ 處 | **未定義** |
| `14px` | 10+ 處 | **未定義**（workflow 卡牌） |

**建議調整：**
```
--radius-xs:  4px   (標籤)
--radius-sm:  6px   (按鈕、輸入框)
--radius-md:  8px   (搜尋框、篩選)
--radius-lg:  12px  (卡牌、面板)
--radius-xl:  14px  (大卡牌、Modal)
--radius-2xl: 20px  (特殊元件)
```

---

## 四、陰影（box-shadow）問題

### 4.1 已定義 vs 實際使用

| 已定義 | 值 | 使用情況 |
|--------|-----|---------|
| `--shadow-sm` | `0 1px 4px rgba(26,154,170,0.07)` | ⚠️ 偶爾使用 |
| `--shadow-md` | `0 4px 16px rgba(26,154,170,0.09)` | ⚠️ 偶爾使用 |
| `--shadow-lg` | `0 8px 32px rgba(26,154,170,0.12)` | ❌ 幾乎不用 |
| `--shadow-xl` | `0 16px 48px rgba(26,154,170,0.15)` | ❌ 不使用 |

**問題：大多數陰影是硬編碼的，且使用 `rgba(0,0,0,...)` 而非主題色**

| 硬編碼值 | 檔案 | 用途 |
|---------|------|------|
| `0 4px 16px rgba(0,0,0,0.06)` | admin.css | 卡牌 hover |
| `0 6px 20px rgba(0,0,0,0.08)` | workflow.css | 工作流卡牌 hover |
| `0 8px 32px rgba(0,0,0,0.18)` | workflow.css | 刪除 Modal |
| `0 12px 40px rgba(0,0,0,0.18)` | workflow.css | 儲存 Modal |
| `-4px 0 24px rgba(0,0,0,0.12)` | admin.css | Drawer |

---

## 五、動畫/過渡問題

### 5.1 定義 vs 使用

已定義：`--transition: 0.20s cubic-bezier(0.4, 0, 0.2, 1)`

硬編碼的不同時長：

| 時長 | 檔案 | 元件 | 應該是 |
|------|------|------|--------|
| `0.12s` | admin.css | Nav hover | 偏短 |
| `0.15s` | admin.css, workflow.css | 按鈕、卡牌 | 微短 |
| `0.20s` | 多處 | 各種 hover | ✅ 符合 |
| `0.25s` | admin.css | Drawer 滑入 | ✅ 適用動畫 |
| `0.32s` | style.css | Toast | 稍長 |
| `1.5s` | admin.css | Skeleton pulse | ✅ loading 動畫 |

**建議統一：**
- 微互動：`0.15s`（hover、focus）
- 標準過渡：`0.20s`（狀態切換）
- 動畫進出：`0.25s`（Drawer、Modal）
- Loading：`1.5s`（Skeleton、Pulse）

---

## 六、各頁面逐一檢查

### 6.1 login.html
- ✅ 色系基本正確（使用 kway-blue）
- ⚠️ 動畫時長 `0.42s` / `0.7s` 特殊
- ✅ 字體繼承正確

### 6.2 chat.html（對話頁）
- ❌ 15+ 硬編碼顏色
- ❌ `--shadow-clay-*` 未定義
- ❌ `--accent-teal*` 未定義
- ⚠️ 字體大小 20+ 種

### 6.3 chat.html（Skill Editor）
- ⚠️ 儲存狀態視窗 overlay 0.35 透明度
- ⚠️ 刪除確認 overlay 0.45 透明度
- ✅ 按鈕色系使用 kway-blue
- ⚠️ danger 按鈕硬編碼 `#ea4335`

### 6.4 chat.html（Workflow Landing）
- ✅ 三欄佈局正確
- ⚠️ 卡牌顏色硬編碼在 JS `_WF_COLORS` 陣列
- ✅ 篩選/搜尋 UI 一致

### 6.5 chat.html（Workflow Designer）
- ⚠️ Block 顏色全部硬編碼在 workflow.js
- ✅ 連接線顏色使用 CSS `#94A3B8`
- ⚠️ 10px Grid 顏色硬編碼 `rgba(0,0,0,0.03)` 和 `0.1`

### 6.6 admin.html（Dashboard）
- ✅ KPI 卡牌使用 border-left accent 色
- ⚠️ accent 色硬編碼（`#F5A623`、`#059669`、`#DC2626`）
- ✅ Chart.js 顏色與主題搭配

### 6.7 admin.html（Skills 管理）
- ✅ 表格結構正確
- ⚠️ Mode dot 顏色硬編碼在 CSS
- ⚠️ Drawer 60+ 處 inline style
- ✅ Ctrl+K Modal 樣式一致

### 6.8 admin.html（Workflows 管理）
- ✅ Card Grid 樣式正確
- ⚠️ 卡頭顏色硬編碼 JS `_WF_COLORS`
- ✅ Timeline 和 Donut Chart 正確

### 6.9 admin.html（Token Dashboard）
- ✅ Chart.js 圖表顏色正確
- ✅ KPI 卡牌一致
- ⚠️ 切換按鈕 active 狀態用 class 名稱辨識（非色系問題）

### 6.10 admin.html（排程監控）
- ✅ 表格結構正確
- ⚠️ type 顏色硬編碼在 JS `typeColors` 物件
- ✅ Drawer 樣式與 Skills 一致
- ✅ 刪除確認 Modal 樣式一致

### 6.11 admin.html（使用者管理）
- ✅ 排序 icon 正確
- ✅ Admin badge 紅色 + 對齊
- ⚠️ badge 色 `#fef2f2` / `#dc2626` 硬編碼
- ✅ Drawer 編輯表單一致

### 6.12 admin.html（系統設定）
- ✅ 設定卡牌樣式一致
- ✅ badge 狀態色正確
- ⚠️ monospace 路徑文字用 inline style

### 6.13 settings.html（基本資訊）
- ✅ 純文字顯示（已移除 input 框）
- ✅ 部門代號格式正確
- ⚠️ `.page-settings-row-value` 顏色使用 `--text-muted`（一致）

---

## 七、修正優先級

### P0 — 立即修正（破壞性問題）
1. 在 `style.css :root` 補上所有未定義的 CSS 變數
2. 定義狀態色變數（success/error/warning/info）
3. 定義 `--bg-main` 和 `--text-tertiary`

### P1 — 高優先（視覺不一致）
4. 統一字體大小比例（減少到 8 個等級）
5. 統一圓角比例
6. 統一 Modal overlay 透明度為 0.40
7. 統一邊框 rgba 透明度為 0.08

### P2 — 中優先（維護性）
8. 將 workflow.js Block 顏色抽為 CSS 變數
9. 將 admin.js inline style 抽為 CSS class
10. 統一陰影使用 `--shadow-*` 變數

### P3 — 低優先（Dark Mode 準備）
11. 確保所有硬編碼色都有 `[data-theme="dark"]` 覆寫
12. Chart.js 顏色改為讀取 CSS 變數
