# MCP_Server 重構規劃報告（整合版）

## 1. 規劃決議

依你的指示，本次重構採用以下原則：

1. 目標檔案與資料夾架構：採用 v3 報告的「4. 建議新架構」。
2. 腳本拆分方式：由本報告重新規劃，採低風險漸進式拆分。

---

## 2. 目標架構（採用 v3 版本）

```text
MCP_Server/
│
├── server/
│   ├── app.py
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── chat.py
│   │   ├── documents.py
│   │   ├── skills.py
│   │   └── models.py
│   ├── core/
│   │   ├── uma_core.py
│   │   ├── session.py
│   │   ├── retriever.py
│   │   ├── executor.py
│   │   ├── converter.py
│   │   └── watcher.py
│   ├── adapters/
│   │   ├── base.py
│   │   ├── factory.py
│   │   ├── openai_adapter.py
│   │   ├── gemini_adapter.py
│   │   └── claude_adapter.py
│   ├── nlp/
│   │   ├── __init__.py
│   │   ├── tokenizer.py
│   │   └── tool_selector.py
│   ├── integrations/
│   │   └── line_connector.py
│   ├── dependencies/
│   │   ├── __init__.py
│   │   ├── uma.py
│   │   ├── session.py
│   │   └── retriever.py
│   └── schemas/
│       ├── chat.py
│       ├── skills.py
│       └── documents.py
│
├── frontend/
│   ├── index.html
│   ├── src/
│   │   ├── js/
│   │   │   ├── modules/
│   │   │   │   ├── chat.js
│   │   │   │   ├── skills.js
│   │   │   │   ├── documents.js
│   │   │   │   └── ui.js
│   │   │   ├── api.js
│   │   │   └── i18n.js
│   │   └── css/
│   │       ├── tokens.css
│   │       ├── components.css
│   │       ├── layout.css
│   │       └── dark.css
│   └── assets/
│       └── images/
│
├── Agent_skills/
├── docs/
│   ├── architecture/
│   └── reports/
├── tests/
├── scripts/
├── memory/
├── workspace/
├── tmp/
├── .env
├── .env.template
├── .gitmodules
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## 3. 腳本拆分規劃（新制定）

### 3.1 後端 Python 腳本拆分策略

1. `router.py` 只保留相容轉發，實作移至 `server/routes/*`。
2. 共用邏輯先抽出，再切路由，避免重複與循環依賴。
3. `line_connector.py` 改為 `server/integrations/line_connector.py`，透過 `server/dependencies/*` 取得 UMA、Session、Retriever。

### 3.2 `router.py` 拆分邊界（建議）

1. `server/routes/chat.py`：`/chat`、`/chat/session/*`、`/chat/flush/*`、`/execute`
2. `server/routes/documents.py`：`/api/documents/*`、`/api/research`
3. `server/routes/skills.py`：`/skills/*`
4. `server/routes/models.py`：`/api/models`、`/health`

### 3.3 前端 `app.js` 拆分邊界（建議）

1. `frontend/src/js/modules/chat.js`：訊息流、會話呈現、送訊息流程
2. `frontend/src/js/modules/skills.js`：技能清單、技能 CRUD、技能抽屜
3. `frontend/src/js/modules/documents.js`：上傳、預覽、workspace 檔案互動
4. `frontend/src/js/modules/ui.js`：modal/toast/alert 與 UI 工具
5. `frontend/src/js/api.js`：統一 `fetch` API 呼叫

### 3.4 CSS 拆分邊界（建議）

1. `tokens.css`：設計 token 與顏色/字體/間距變數
2. `layout.css`：三欄框架與頁面骨架
3. `components.css`：元件樣式（button/card/list/modal）
4. `dark.css`：`prefers-color-scheme: dark` 覆寫

### 3.5 scripts 目錄職責重整

1. `scripts/dev/`：啟動、除錯、環境準備
2. `scripts/skills/`：技能初始化、封裝、版本工具
3. `scripts/tests/`：測試輔助腳本（例如 webhook 測試）
4. `scripts/migration/`：一次性搬移與重構輔助腳本

---

## 4. 分階段執行（含腳本拆分）

1. Phase 0（0.5-1 天）：路徑與設定一致化（`SKILLS_HOME`、compose、.env.template）
2. Phase 1（2-3 天）：建立 `server/` 骨架 + 搬移 `core/`、`adapters/`（先不改邏輯）
3. Phase 2（2-4 天）：拆分 `router.py` -> `routes/*` + `dependencies/*` + `schemas/*`
4. Phase 3（2-3 天）：`static` 重組為 `frontend`，拆分 `app.js` 與 `style.css`
5. Phase 4（1-2 天）：`scripts` 分類重整 + 文件更新 + 回歸測試

---

## 5. 驗收標準

1. 後端路由已依 domain 拆分，`router.py` 不再承載主要邏輯。
2. 前端 JS/CSS 完成模組化拆分，`app.js`、`style.css` 不再單檔巨型維護。
3. `scripts` 已依職責分層，臨時與正式腳本可明確區分。
4. Docker 啟動、聊天、技能管理、文件上傳、LINE webhook 功能可正常運作。

---

## 6. 立即下一步（建議）

1. 先執行 Phase 0，統一 `SKILLS_HOME` 與啟動入口規劃。
2. 由後端 `router.py` 拆分先行（風險最高、收益最大）。
3. 前端與 scripts 拆分可在後端拆分穩定後並行進行。

