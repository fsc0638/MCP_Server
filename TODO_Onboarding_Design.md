# TODO: AgentK 新用戶 Onboarding 設計

> 建立日期：2026-04-09
> 狀態：待實作
> 資料來源：workspace/department/同仁清單.xlsx（148 人、21 部門）

---

## 資料結構

### 同仁清單欄位
| 欄位 | 範例 |
|------|------|
| # | 1 |
| 同仁姓名 | 0337　羅燕秋 |
| 分機 | 108 |
| 部門 | A100　稽核室 |
| 職稱 | 高級專員 |
| 信箱 | rachel@mail.kway.com.tw |

### 部門清單（21 個）
- A000 董事長室
- A100 稽核室
- A200 董總辦公室
- B000 總經理室
- B100 (總)-交易所專案處
- C110 管理處
- C120 財務處
- C130 資訊處
- C140 公關暨專案室
- T000 第一事業群
- T100 第二事業群
- T140 (一)業務處
- T200 (一)產品開發一處
- T210 (一)帳務產品處
- T230 (一)產品服務處
- T240 (二)業務處
- T250 (二)產品開發處
- T260 (二)產品服務處
- T610 (三)創新產品處
- Y000 研發中心
- Y200 研發中心-開發處

---

## Web UI Onboarding 流程

### 觸發條件
- LINE Login 成功後，檢查 `workspace/users/{user_id}.json` 是否存在
- 不存在 → 導向 onboarding 頁面
- 存在 → 直接進入 chat.html

### Step 1: 歡迎頁（AgentK 自我介紹）
- [ ] AgentK Logo + 歡迎文字
- [ ] 功能簡介（行程管理/搜尋/文件/自動化）
- [ ] 「開始設定」按鈕

### Step 2: 身份驗證（從同仁清單匹配）
- [ ] 輸入信箱 或 姓名搜尋
- [ ] 自動從 同仁清單.xlsx 匹配：姓名、部門、職稱、分機
- [ ] 匹配成功 → 自動填入，用戶確認
- [ ] 匹配失敗 → 手動填寫（訪客/外部人員）

### Step 3: 使用偏好
- [ ] 偏好語言：繁體中文 / English / 日本語 / Tiếng Việt
- [ ] 回覆風格：簡潔 / 適中 / 詳盡
- [ ] 主要用途（多選）：搜尋/文件/行程/開發/排程/其他

### Step 4: 完成
- [ ] 歡迎動畫 + 個人化問候
- [ ] 儲存 user context → workspace/users/{user_id}.json
- [ ] 初始化 profile
- [ ] 跳轉 chat.html

---

## LINE Bot Onboarding 流程

### 觸發條件
- Follow event（用戶加好友）
- 或首次在群組中 @Agent K

### 歡迎訊息
```
嗨！我是 AgentK 👋
凱衛資訊的 AI 智能助理

我可以幫你管理行程、搜尋資料、分析文件、自動化工作流程 🚀

在開始之前，請告訴我你的信箱或姓名，
讓我從公司系統中找到你的資料！

例如：rachel@mail.kway.com.tw
或：羅燕秋
```

### 匹配流程
- [ ] 用戶回覆信箱/姓名 → 從同仁清單匹配
- [ ] 匹配成功 → 確認身份 + 儲存
- [ ] 匹配失敗 → 詢問是否為外部人員 → 手動填寫

---

## 後端 API

- [ ] `GET /api/onboarding/departments` — 回傳部門清單
- [ ] `POST /api/onboarding/lookup` — 用信箱/姓名查詢同仁清單
- [ ] `POST /api/onboarding/complete` — 儲存 user context
- [ ] `GET /api/onboarding/status/{user_id}` — 檢查是否已完成 onboarding

---

## User Context 存儲格式

```json
{
  "user_id": "U09e3122dcc...",
  "employee_id": "0638",
  "name": "范書愷",
  "department": "Y000 研發中心",
  "title": "高級工程師",
  "email": "fsc@mail.kway.com.tw",
  "extension": "302",
  "preferences": {
    "language": "繁體中文",
    "style": "適中",
    "primary_use": ["搜尋", "開發", "排程"]
  },
  "role": "editor",
  "onboarding_completed": true,
  "created_at": "2026-04-09T...",
  "source": "line_login"
}
```

---

## 前端頁面

- [ ] `frontend/pages/onboarding.html` — Onboarding 專用頁面
- [ ] `frontend/assets/css/onboarding.css` — 樣式
- [ ] `frontend/assets/js/onboarding.js` — 邏輯
- [ ] 修改 `auth.py` LINE Login callback → 檢查 onboarding status → 決定跳轉
