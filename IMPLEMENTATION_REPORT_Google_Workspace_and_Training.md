# AgentK 實作報告：Google Workspace 整合 + 教育訓練互動系統

> 建立日期：2026-04-06
> 版本：1.0.0

---

## 一、五層架構對應

```
┌─────────────────────────────────────────────────────────────┐
│  第1層：情境 (Context)                                        │
│  「企業在做什麼？價值在哪裡產生？」                                │
│  ── references/ 資料夾（知識文件注入）                            │
├─────────────────────────────────────────────────────────────┤
│  第2層：隱形前端 (Invisible Interface)                         │
│  「人類不需要學軟體，用自然語言下達意圖」                           │
│  ── LINE / Web UI 純文字輸入輸出                               │
├─────────────────────────────────────────────────────────────┤
│  第3層：AI Agent 核心 (Language + Reasoning + Understanding)  │
│  ── LLM-as-a-Router → Model Adapter → Tool Calling Loop     │
├─────────────────────────────────────────────────────────────┤
│  第4層：Skills + 材料 (Execution Layer)                       │
│  ── CLI, API, 文件分析, 排程, Google Workspace                │
├─────────────────────────────────────────────────────────────┤
│  第5層：企業安全分級 (Governance)                               │
│  ── risk_level: high → Auth Modal 攔截                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、新增 Skill 清單

| Skill | 模式 | Risk | Timeout | 用途 |
|-------|------|------|---------|------|
| mcp-gai-worksheet-facilitator | Semantic | low | — | 教育訓練引導 |
| mcp-google-calendar | Executable | high | 30s | 日曆 CRUD |
| mcp-google-meet | Executable | high | 30s | Meet 連結建立 |
| mcp-google-gmail | Executable | high | 30s | 信件收發搜尋 |
| mcp-google-calendar-digest | Executable | low | 30s | 排程推播行程摘要 |

---

## 三、共用 OAuth 授權層

新增 `server/services/google_auth.py`：
- 從 `workspace/credentials/{session_id}_google.json` 載入 credential
- 自動 refresh token
- 提供 `get_google_credentials(session_id)` 給所有 Google Skill 使用

環境變數：
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_OAUTH_REDIRECT_URI`

---

## 四、新增依賴

```
google-api-python-client>=2.90.0
google-auth-oauthlib>=1.2.0
google-auth-httplib2>=0.2.0
```

---

## 五、教育訓練閉環

```
簡報 → 發學習單 → 學員加 LINE → AgentK 引導對話 → 完成學習單 → 講師評分
```

學員體驗本身就是五層架構的活教材。

---

## 六、開發優先序

| Phase | 內容 |
|-------|------|
| P0 | OAuth 授權層 + mcp-gai-worksheet-facilitator |
| P1 | mcp-google-calendar + mcp-google-calendar-digest |
| P2 | mcp-google-meet |
| P3 | mcp-google-gmail |
