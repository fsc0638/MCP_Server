# Workflow V2 — Phase 1〜6 測試案例與檢核清單

> 版本：fsc `648ef2f` 之後
> 測試目的：一次跑完 6 個 Phase 的所有機制，找出需調整的項目
> 建議測試帳號：至少 2 個（1 個 admin + 1 個一般使用者）

---

## 執行前準備

| 步驟 | 指令 / 動作 | 預期結果 |
|---|---|---|
| 1. 啟動服務 | `python main.py` | port 8500 正常監聽，啟動 log 出現 `[WorkflowSchema] migrate_on_startup` |
| 2. 備份 | 複製 `workspace/workflows/` 到 `workspace/workflows_backup/` | 可隨時 rollback |
| 3. 準備環境變數 | `.env` 至少要有 `OPENAI_API_KEY`、`TAVILY_API_KEY`（少缺一個可以測 Gate 1）| 後續部分案例會依賴 |
| 4. 清 promotion queue | 刪除 `workspace/workflows/promotion_queue.json`（若存在）| Phase 6 從乾淨狀態開始 |
| 5. 開啟 DevTools | Chrome → F12 → Console + Network | 觀察 SSE、422/428/403 |

---

# Phase 1 — Workflow Schema v2 + 遷移

**目標**：新的 workflow 一律以 v2 JSON Schema 落盤；舊檔自動 migrate。

## Test Case 1.1 — 啟動自動遷移
**前置**：`workspace/workflows/` 底下仍留一個舊版 flat JSON（沒有 `definitions` / `steps`）。
**步驟**：重啟 server。
**檢核**：
- [ ] server 啟動 log 出現 `[WorkflowSchema] Migrated N legacy workflow(s)`
- [ ] 原 flat JSON 被移到 `workspace/workflows/legacy_backup/{timestamp}/`
- [ ] 對應的新檔案出現在 `workspace/workflows/personal/{owner}/{id}.json`
- [ ] 打開新檔案：有 `workflow_id`, `display_name`, `version: "2.0"`, `steps[]`, `variables.definitions[]`

## Test Case 1.2 — Workflow ID 允許 `wf-xxx` 格式
**步驟**：POST `/api/workflows` 新建一個 id 為 `wf-test-alpha` 的 workflow。
**檢核**：
- [ ] 回應 200
- [ ] `GET /api/workflows/wf-test-alpha?scope=personal&owner={self}` 回 200（不會 404）
- [ ] 儲存後檔名為 `wf-test-alpha.json`（連字號保留）

## Test Case 1.3 — display_name 出現在卡片上
**步驟**：前往 workflow 列表頁。
**檢核**：
- [ ] 卡片顯示中文名（來自 `display_name`），**不是** slug
- [ ] 卡片上變數計數 = `variables.definitions[]` 的長度（而非 dict 的 key 數）

## Test Case 1.4 — Schema 驗證失敗時的訊息
**步驟**：用 Postman / curl 直接 POST 一個 `execution.on_error: "unknown_value"` 的 workflow。
**檢核**：
- [ ] 回應 422，body 含 `errors` 陣列
- [ ] 訊息指出違反的是 `execution.on_error` enum

---

# Phase 2 — 四層 Gate 檢核

**目標**：在儲存 / 執行 / 步驟 / 收尾 4 個階段擋掉不合法狀態。

## Test Case 2.1 — Gate 0 拒絕空白 workflow
**步驟**：
1. 在 UI 點「新增工作流」→ 直接 **不加任何 skill 區塊** → 點「儲存」
2. 第二次測：加 1 個 skill 但 display_name 留 `新工作流` → 儲存
**檢核**：
- [ ] 兩次都回 422
- [ ] UI toast 顯示具體錯誤（例如「請至少新增一個技能區塊」、「請填寫名稱」），**不是**「已暫存於本機」
- [ ] 重整頁面後，該 workflow **不存在**（沒有 orphan stub）

## Test Case 2.2 — Gate 1 缺環境變數（硬擋）
**前置**：`.env` 移除 `TAVILY_API_KEY`（或臨時改名）→ 重啟 server。
**步驟**：建一個包含 `mcp-web-search` 的 workflow → 儲存 → 點「執行」
**檢核**：
- [ ] 回應 422（非 500）
- [ ] Toast 顯示「❌ 缺少環境變數：TAVILY_API_KEY」**具體訊息**
- [ ] Server log 看到 `[Gate1] blocked: missing_env`
- [ ] 不啟動執行流程（不寫 run log）

## Test Case 2.3 — Gate 1 缺 user_input（軟擋 + Wizard）
**步驟**：
1. 建立 workflow，新增一個變數 `searchQuery`（source=user_input, required=true, default 留空）
2. 儲存 → 點「執行」
**檢核**：
- [ ] 回應 428（不是 422，不是 500）
- [ ] **彈出 Inputs Wizard modal**，不是瀏覽器原生 `prompt()` 對話框
- [ ] Wizard 內可以輸入值，按確認後執行成功
- [ ] 變數名 `searchQuery`（**沒有前導空格**；若有 trim bug 會看到 ` searchQuery`）

## Test Case 2.4 — Gate 1 同時檢查 skill 自己的 env_requirements
**前置**：某技能的 `SKILL.md` 有 `env_requirements: [SOMETHING_NEEDED]`，但 `.env` 沒定義。
**步驟**：把該 skill 加到 workflow → 執行。
**檢核**：
- [ ] Gate 1 也會擋（不是只看 skill registry 的 Python 套件檢查）
- [ ] 訊息指出是哪個 skill 要哪個變數

## Test Case 2.5 — Gate 3 執行失敗顯示具體錯誤
**步驟**：不修正 TAVILY_API_KEY，強制執行（若能通過 Gate 1），或使用會拋錯的 skill。
**檢核**：
- [ ] Toast 顯示 `❌ mcp-web-search失敗：Missing TAVILY_API_KEY...`（第一個錯誤的實際訊息）
- [ ] **不是** 只有「0 成功 / 1 失敗」的計數

## Test Case 2.6 — Gate 3 寫入 run log
**步驟**：成功執行一個 workflow。
**檢核**：
- [ ] `workspace/workflows/runs/{workflow_id}/{run_id}.json` 存在
- [ ] 內容有 `run_id`, `status`, `started_at`, `finished_at`, `steps`

---

# Phase 3 — Router 可解釋輸出

**目標**：Workflow-First Matcher 不只給 ID，還給信心分數、原因、被排擠的候選。

## Test Case 3.1 — favicon.ico 不再 404
**步驟**：打開 http://localhost:8500/ui，F12 → Network。
**檢核**：
- [ ] `favicon.ico` 回 200（不是 404）
- [ ] 沒有紅字 `INFO ... 404 Not Found` 噪音

## Test Case 3.2 — Auto 模式：match_info 帶回解釋
**前置**：建立一個帶 `trigger.patterns: ["每日新聞"]`、`trigger.mode: "auto"` 的 workflow。
**步驟**：在聊天輸入「幫我跑每日新聞」。
**檢核**：
- [ ] SSE `status: "success"` payload 中 `workflow_match` 包含：
  - `id`, `name`, `method`（`pattern` / `semantic` / `fulltext`）
  - `confidence`（0〜1）
  - `match_reason`（字串）
  - `rejected_candidates[]`（最多 3 個，每個有 id + score）

## Test Case 3.3 — Confirm 模式：顯示步驟預覽
**前置**：把上面的 workflow 改成 `trigger.mode: "confirm"`。
**步驟**：同 3.2。
**檢核**：
- [ ] 聊天回覆內有 **步驟預覽**：`mcp-web-search → ...`
- [ ] 包含 `confidence` 與 `match_reason` 說明
- [ ] 使用者可以「確認執行」或「取消」

## Test Case 3.4 — 語意 Top-5
**步驟**：輸入一個 **接近但不完全符合** 的 query。
**檢核**：
- [ ] Server log 有 `[WF-First] Semantic top-5: [...]` 列出 5 個候選 + score
- [ ] 最後只挑分數最高者；其他出現在 `rejected_candidates`

---

# Phase 4 — 並行分支 + 子工作流 + 循環偵測

**目標**：執行器支援 parallel、sub_workflow 類型；防止無限遞迴。

## Test Case 4.1 — Parallel 分支
**前置**：建立 workflow 含一個 `type: "parallel"` 節點，兩個分支各放不同 skill（例：分支 A = mcp-web-search, 分支 B = mcp-python-executor）。
**步驟**：執行。
**檢核**：
- [ ] 兩個分支 **同時** 開跑（log 時間戳幾乎相同）
- [ ] 最終 run log 中有兩個 branch 的結果
- [ ] 最終 final_output 匯總兩者

## Test Case 4.2 — Sub-workflow 呼叫
**前置**：A workflow 引用 B workflow（`type: "sub_workflow"`, `workflow_id: "B"`）。
**步驟**：執行 A。
**檢核**：
- [ ] Log 看到 `[WFExec] Start: B run=... (stack_depth=1)`
- [ ] B 的 run log 也被寫入
- [ ] B 的結果作為 A 的 step output 回寫

## Test Case 4.3 — 循環偵測
**前置**：A → B → A（刻意做循環）。
**步驟**：執行 A。
**檢核**：
- [ ] 當 stack_depth > 5 時，執行器 **中止**
- [ ] Log 出現 `cycle detected` 或類似訊息
- [ ] 回應帶錯誤，但 **不會把 server crash**

## Test Case 4.4 — 階層執行堆疊共享
**檢核**：
- [ ] `WorkflowExecutor._execution_stack` 是 class-level
- [ ] 並行分支也算在同一個 stack

---

# Phase 5 — 5-問題 Wizard + 模板庫

**目標**：不懂 YAML 的使用者可以用「問答」建立 workflow。

## Test Case 5.1 — Wizard UI 5 步驟
**步驟**：UI 點「新增工作流」→ 選「引導式建立」。
**檢核**：
- [ ] 依序出現 5 個問題：
  - Q1 目的（新聞 / 資料整理 / 會議整理 / 備忘）
  - Q2 輸入來源（文字 / 檔案 / 網址 / 日曆 / LINE）
  - Q3 輸出去向（摘要 / Notion / LINE 推播 / Email）
  - Q4 排程（手動 / 每日 / 每週 / 每月）
  - Q5 失敗時（retry / skip / notify）
- [ ] 每題可選可跳過
- [ ] 最後進入 review 畫面，顯示配到的模板名稱

## Test Case 5.2 — 模板匹配是確定性的（不靠 LLM）
**步驟**：送 `POST /api/workflows/wizard` `{"purpose":"新聞","output_target":"LINE 推播","schedule":"每日"}`
**檢核**：
- [ ] 回應 200，挑到 `daily_digest`（每日新聞簡報）
- [ ] 多次送同樣 payload 會拿到同一模板（穩定）
- [ ] 不會 call OpenAI（沒有 API cost）

## Test Case 5.3 — 所有模板都能列出
**步驟**：`GET /api/workflows/templates`
**檢核**：
- [ ] 至少回傳 6 個模板（對應 `workspace/workflows/templates/`）
- [ ] 每個模板都有 `template_id`, `display_name`, `icon`, `question_match`

## Test Case 5.4 — Wizard 產出的 workflow 可直接儲存
**步驟**：Wizard 完成 → 按「儲存」。
**檢核**：
- [ ] 通過 Gate 0
- [ ] 能在列表出現
- [ ] 能正常執行

---

# Phase 6 — One-shot 升格

**目標**：LLM 臨時生成的 workflow 執行成功後，使用者可一鍵永久儲存。

## Test Case 6.1 — promotion_queue 寫入
**前置**：要有一個 `source: "llm_generated"` 的 workflow（目前無 UI 產生器，**手動製作**：編輯一個現有 workflow 的 JSON，把 `source` 改成 `"llm_generated"`）。
**步驟**：執行該 workflow。
**檢核**：
- [ ] 成功後 `workspace/workflows/promotion_queue.json` 出現新條目
- [ ] 條目有：`run_id`, `workflow_id`, `display_name`, `original_prompt`, `final_output_preview`（≤200 字）, `steps[]`, `at`
- [ ] `workspace/workflows/oneshot/{run_id}.json` 也被寫入（完整 snapshot）

## Test Case 6.2 — 前端升格卡片顯示
**步驟**：同上，但從聊天頁觸發。
**檢核**：
- [ ] 執行完成的氣泡下方出現紫色虛線卡片
- [ ] 卡片有「儲存為我的工作流」與「略過」兩個按鈕
- [ ] 只對 `source === "llm_generated"` 的 workflow 顯示（系統 / 模板的不會）

## Test Case 6.3 — 儲存 Modal
**步驟**：點「儲存為我的工作流」。
**檢核**：
- [ ] 彈出 modal，欄位：名稱（必填）、描述（選填）、scope（personal/department/system）
- [ ] 名稱自動預填 workflow 原名
- [ ] 點遮罩可關閉
- [ ] 名稱空白時點「儲存」會 toast 紅字提示

## Test Case 6.4 — POST /api/workflows/promote
**步驟**：填名稱「測試升格」、scope=personal、確認。
**檢核**：
- [ ] 回應 200，body 有 `workflow_id`, `scope`, `path`
- [ ] 檔案實際存在於 `workspace/workflows/personal/{self_id}/{workflow_id}.json`
- [ ] 內容有 `metadata.promoted_from: {run_id}` 與 `metadata.promoted_at`
- [ ] `promotion_queue.json` 中該 `run_id` 被移除
- [ ] Toast「✅ 已儲存為工作流「測試升格」」
- [ ] 重整 workflow 列表，能看到這個新工作流

## Test Case 6.5 — 權限：非 admin 不能存到 system
**步驟**：同 6.4，但 scope 選「系統工作流」。
**檢核**：
- [ ] 回應 403
- [ ] Toast：「系統層級的 工作流 僅限管理員⋯」

## Test Case 6.6 — target_owner 空字串自動補
**檢核**：前端只送 `target_scope: "personal"`（不送 owner），後端從 session 補 user_id：
- [ ] 回應 200（不會 422「personal scope 需要 owner」）
- [ ] 儲存位置正確

## Test Case 6.7 — GET /api/workflows/oneshot/pending
**步驟**：`curl -b cookies.txt http://localhost:8500/api/workflows/oneshot/pending`
**檢核**：
- [ ] 回應 `{candidates: [...]}`，最多 20 筆，按時間新→舊
- [ ] 只會看到 **自己** 產生的（其他 user 的看不到）

## Test Case 6.8 — GET /api/workflows/oneshot/accessible-skills
**步驟**：`curl -b cookies.txt http://localhost:8500/api/workflows/oneshot/accessible-skills`
**檢核**：
- [ ] 回應 `{skills: [...], count: N}`
- [ ] 每個 skill 有 `skill_id`, `display_name`, `description`, `parameters`, `env_ready`, `risk_level`
- [ ] 未登入 user / 無權限 skill 不會出現

## Test Case 6.9 — oneshot/ 目錄自動清理
**步驟**：連續執行 > 100 個 llm_generated workflow。
**檢核**：
- [ ] `workspace/workflows/oneshot/` 最多保留最新 100 個 snapshot
- [ ] 舊的被自動刪除

---

# 綜合檢核清單（一次性勾選）

## Schema / 遷移
- [ ] 啟動 log 顯示 migrate 次數
- [ ] 所有舊檔被搬到 `legacy_backup/`
- [ ] 新 workflow 為 v2 格式
- [ ] `wf-` 前綴（連字號）ID 可儲可讀

## Gate 0（儲存守門）
- [ ] 無 skill 區塊 → 422
- [ ] 名稱為預設「新工作流」→ 422
- [ ] 合法 workflow → 200

## Gate 1（執行前守門）
- [ ] 缺環境變數 → 422（硬擋）
- [ ] 缺 user_input → 428（軟擋，彈 wizard）
- [ ] Skill 自己的 env_requirements 也檢查
- [ ] Wizard 填完可以繼續執行

## Gate 2（每步守門）
- [ ] 步驟中引用未定義變數會擋下
- [ ] 錯誤訊息指出是哪個 step + 哪個變數

## Gate 3（收尾）
- [ ] 成功 → runs/{id}/{run_id}.json 寫入
- [ ] llm_generated + success → promotion_queue + oneshot snapshot
- [ ] 執行失敗 toast 顯示具體第一個錯誤

## Router
- [ ] favicon 不再 404
- [ ] match_info 含 id/name/method/confidence/reason/rejected
- [ ] confirm 模式顯示 step preview

## Parallel / Sub-workflow
- [ ] 兩分支同時跑
- [ ] 子 workflow 被呼叫、log 獨立
- [ ] 循環被偵測並中止

## Wizard / 模板
- [ ] 5 步驟問答可用
- [ ] 確定性模板匹配（不呼叫 LLM）
- [ ] 至少 6 個模板可選

## One-shot 升格
- [ ] 升格卡片只對 llm_generated 顯示
- [ ] scope picker 三選一
- [ ] target_owner 自動補
- [ ] 非 admin 存 system → 403
- [ ] promotion_queue 升格後自動清
- [ ] accessible-skills 只回權限內技能

---

# 已知限制 / 非本次範圍

- **LLM one-shot 產生器本身** 目前沒有 UI 入口，Phase 6 只鋪好了鐵軌：
  - 需要手動編輯 `source: "llm_generated"` 來觸發升格流程
  - 產生器（根據使用者自然語言 → 自動挑選 accessible skills → 組裝 workflow JSON）是下一個 Phase 的工作
- **權限搬移功能**（personal → department → system 的 promote）僅在 Phase 6 的 `/promote` 端點中實作，UI 上沒有獨立「搬家」按鈕
- **Workflow 版本控制**（`workspace/workflows/versions/`）已有目錄但 UI diff 未完成

---

# 回報格式建議

測完一輪後，整理成：

```
## 測試結果
- ✅ Phase 1: 1.1 / 1.2 / 1.3 通過；1.4 未測
- ❌ Phase 2: 2.3 的 wizard 沒彈出（反而跳到 prompt()）
- ⚠️ Phase 6: 6.4 儲存後路徑少了 employee_id
- ...

## 待修正（按優先序）
1. P1 — Phase 2.3 wizard 未彈出
2. P2 — ...
```

我會依照這份清單逐一修正。
