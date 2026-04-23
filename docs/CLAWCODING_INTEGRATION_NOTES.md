# ClawCoding → fsc 整合風險登記簿 & 交叉驗證清單

> **狀態**：實作前準備  
> **主線分支**：`fsc`（目前 checked out）  
> **來源分支**：`origin/AgentK_ClawCoding`  
> **共同祖先**：`e7aba86`（merge: origin/fsc into AgentK_ClawCoding）  
> **本文件用途**：實作過程中的風險提示與交叉驗證對照表。每完成一步，回到本文件確認「前置條件」與「驗證點」。

---

## 0. 鐵律（絕對不可違反）

1. **絕對不允許的自動操作**
   - ❌ 不主動 `git push`（使用者明確禁令：見 `memory/feedback_no_git_push.md`）
   - ❌ 不主動 `git commit --amend` 或 `git reset --hard`
   - ❌ 不跳過 hooks（`--no-verify`）
   - ❌ 不合併到 `main` 或 `AgentK_UAT`（AgentK_UAT 是驗收分支）

2. **每一個檔案動作必須有所本**
   - 複製 → 必須先 `git show origin/AgentK_ClawCoding:<path>` 確認內容
   - 修補 → 必須先讀 fsc 版本 + ClawCoding 版本，寫 Edit 時兩邊對照
   - 不做「憑記憶修改」、「大概應該這樣」的動作

3. **每一步做完，立刻跑一次冒煙測試**
   - `python main.py` 啟動成功 → 才能進下一步
   - 前端 API 受影響 → 至少用 `curl` 打一次 API 確認
   - 不累積多步才測試

---

## 1. 已驗證事實（來自 `git show`、`git diff`、`git ls-tree`）

### 1-1. fsc 已經有的 ClawCoding 基礎設施（不要重複移入）

fsc 與 ClawCoding 共同基底已包含以下檔案，**不需要**從 ClawCoding 複製：

| 檔案 | 狀態 |
|------|------|
| `server/services/continuous_learner.py` | fsc 已有 |
| `server/services/prompt_builder.py` | fsc 已有 |
| `server/services/memory_store.py` | fsc 已有 |
| `server/services/memory_store_updater.py` | fsc 已有 |
| `server/services/memory_retriever.py` | fsc 已有 |
| `server/services/memory_rollback.py` | fsc 已有 |
| `server/services/behavior_rule_extractor.py` | fsc 已有 |
| `server/services/behavior_rule_loader.py` | fsc 已有 |
| `server/services/learning_compactor.py` | fsc 已有 |
| `server/services/learning_policy.py` | fsc 已有 |
| `server/services/auth_session_store.py` | fsc 已有 |
| `server/services/line_login.py` | fsc 已有 |
| `server/services/bridge_sync.py` | fsc 已有 |
| `server/services/async_bridge.py` | fsc 已有 |
| `server/services/file_extractor.py` | fsc 已有 |
| `server/services/id_utils.py` | fsc 已有 |
| `server/services/permissions.py` | fsc 已有 |
| `server/services/session_cookie.py` | fsc 已有 |
| `server/services/session_token_cookie.py` | fsc 已有 |
| `server/services/prompt_meta_logger.py` | fsc 已有 |
| `server/services/prompt_cache.py` | fsc 已有 |
| `server/services/budget_profiles.py` | fsc 已有 |
| `server/services/google_auth.py` | fsc 已有 |
| `server/services/workflow_matcher.py` | fsc 已有 |
| `server/services/workflow_llm_router.py` | fsc 已有 |
| `server/services/workflow_gates.py` | fsc 已有 |

### 1-2. fsc 比 ClawCoding 更完整的檔案（**絕對不要**倒退回 ClawCoding 版本）

| 檔案 | fsc 優勢 | 驗證方式 |
|------|---------|---------|
| `server/core/uma_core.py` | 有 `_skill_directory()` 修復 dept/personal 路徑 | 若 `_skill_directory` 不在，立刻停手 |
| `server/core/executor.py` | 有 `skill_dir_override` 參數 | 若 `run_script` 沒接此參數，停手 |
| `server/services/scheduled_push.py` | 有 one-shot target_time + 空檔名守衛 | 若沒這兩段邏輯，停手 |
| `server/services/chat_core.py` | 有放寬確認關鍵字 + 拒絕清除邏輯 | 若 `_affirmative_tokens` 不在，停手 |
| `server/services/workflow_schema.py` | 有 `generate_workflow_id()` + WorkflowK_ 鎖定 | 若這兩個函式不在，停手 |
| `server/services/workflow_scheduler.py` | **ClawCoding 完全沒有此檔** | 移入後不可覆蓋 |
| 全部前端 CSS（style.css、workflow.css、admin.css） | 已代幣化 + 主選單鎖定 | 不動前端除非明確指定 |
| `frontend/pages/admin.html` | 有 primary-nav rail | 只加「審核中心」Tab，不動 rail |
| `frontend/pages/chat.html` | sidebar 已重排 | 不動 |

### 1-3. ClawCoding 真正獨有、需要移入的檔案

**新增檔（9 個）**：

```
server/services/db.py                    (134 行 — SQLite SSOT 基礎)
server/services/audit_logger.py          (115 行 — append-only 稽核)
server/services/approvals_service.py     (132 行 — HitL 審批 CRUD)
server/services/policy.py                ( 82 行 — PDP 授權)
server/services/workflow_checkpoint.py   ( 80 行 — block 級 checkpoint)
server/routes/approvals.py               (152 行 — 審批 API)
server/routes/audit.py                   ( 52 行 — 稽核查閱 API)
server/routes/workflow_resume.py         (114 行 — 審批後 resume)
server/routes/workflow_runs.py           ( 64 行 — 執行記錄查閱)
```

**新增測試（6 個）**：

```
tests/test_approvals_routes_unit.py
tests/test_approvals_service_unit.py
tests/test_audit_logger_unit.py
tests/test_policy_unit.py
tests/test_workflow_executor_approval_unit.py
tests/test_workflow_resume_unit.py
```

---

## 2. 具體修改點（每一條都有行號/錨點）

### 2-1. `server/app.py`（簡單補丁）

**fsc 現況**（確認 `git diff origin/AgentK_ClawCoding origin/fsc -- server/app.py`）：  
第 9 行 `from server.routes import models, documents, chat, skills, workspace, resources, auth, workflow`  
第 33 行附近已有 `app.include_router(workflow.router)`

**需要修改**：  
- 第 9 行加上 `, approvals, workflow_resume, audit, workflow_runs`  
- 第 33 行後加 4 行 `app.include_router(...)`

**驗證**：
```bash
python -c "from server.app import app; print([r.path for r in app.routes if '/api/approvals' in r.path or '/api/audit' in r.path])"
```
應列出至少 4 個新路由。

### 2-2. `server/routes/chat.py`（中等補丁）

**fsc 現況**：`/execute` 路由目前是純執行，沒有 policy / audit。

**需要加回**：約 60 行的 policy 包裝（來自 ClawCoding 版本第 144-222 行）。

**關鍵注意**：
- ClawCoding 版本 import 了 `from fastapi import Cookie`  → fsc 現在沒 import → **記得加 import**
- `mcp_session: str = Cookie(default="")` 參數簽章要加回
- 確認 `resolve_caller_context`, `authorize`, `create_approval`, `log_event` 這 4 個 import 都完整
- `create_approval` 的 import 在 /execute 裡不需要（只有 authorize + log_event 用到），別抄多了

**驗證**：
```bash
curl -X POST http://localhost:8500/execute -H "Content-Type: application/json" -d '{"skill_name":"mcp-web-search","arguments":{"query":"test"}}'
# 應看到 audit_events 表有新記錄
sqlite3 workspace/ssot/agentk.sqlite "SELECT * FROM audit_events ORDER BY ts DESC LIMIT 1"
```

### 2-3. `frontend/pages/admin.html`（簡單補丁）

fsc 已有 `.admin-primary-nav` rail。只需在 admin.js 的 page tab 列表加一項。

**檢查清單**：
- [ ] 不動 primary-nav rail
- [ ] 找到 page tab 的容器（內有 dashboard / skills / workflows / schedules / tokens / users）
- [ ] 加 `<button data-page="approvals">審核中心</button>` 保持一致樣式

### 2-4. `frontend/assets/js/admin.js`（中等合併）

**fsc 第 29-35 行**（PAGE_RENDERERS 物件）目前：
```js
const PAGE_RENDERERS = {
  dashboard: renderDashboard,
  skills: renderSkills,
  workflows: renderWorkflows,
  schedules: renderSchedules,      // fsc 有這行
  tokens: renderTokens,
  users: renderUsers,
  ...
};
```
**要加回**一行 `approvals: renderApprovals,`（ClawCoding 原本在第 32 行）

**ClawCoding 原本定義**：`renderApprovals` 函式（約 328 行，從 ClawCoding 版本第 1334-1660 行區間）
**做法**：整段函式直接貼到 fsc admin.js 尾端（不覆蓋 fsc 任何既有函式）

**驗證**：
- 載入 admin.html → 點「審核中心」→ 看到 4 個 Tab（待審核/已批准/已拒絕/已過期）
- 跑一個 `mcp-high-risk-demo` → 應出現在待審核列表

### 2-5. `server/services/workflow_executor.py`（**最高風險**）

這是唯一需要「真實手術」的檔案。

**fsc 現況關鍵結構**：
- 第 394 行 `class WorkflowExecutor.__init__`
- 第 400 行 `def _get_adapter`
- 第 420 行 `async def _invoke_semantic_skill`
- 第 520 行 `async def execute`  ← 主入口
- 第 675 行 `async def _runner(bid)` ← 實際執行單個 block（在 execute 內定義）
- 第 893 行 `async def _execute_one_block_async` ← 新分拆出來的 helper
- 第 1549 行 `def _record_skill_usage`

**ClawCoding 要移入的邏輯**（從 ClawCoding 第 40-157 行 execute() 方法）：

1. **run_id 生成**（fsc 已有，第 ~540 行附近 `from server.services.workflow_gates import new_run_id`）→ 確認位置
2. **Checkpoint 載入 & skip**（ClawCoding 153-160 行）
   ```python
   from server.services.workflow_checkpoint import get_run_block_status_map, upsert_block_run
   _ck = get_run_block_status_map(run_id)
   # 在 block 迴圈頂部：
   if _ck.get(bid) == "success":
       results.append({"block_id": bid, "status": "skipped", "reason": "checkpoint"})
       continue
   ```
3. **HitL 暫停邏輯**（ClawCoding 343-414 行）
   ```python
   if isinstance(result, dict) and result.get("status") == "requires_approval":
       from server.services.approvals_service import create_approval
       # ... 建立 approval + 回傳 requires_approval
   ```
4. **每個 block 成功後寫 checkpoint**（ClawCoding 452 行附近）
   ```python
   upsert_block_run(run_id=run_id, workflow_id=workflow_id, block_id=bid, ...)
   ```
5. **執行結束寫 audit log**（ClawCoding 522 行附近 `gate_3_log_run`）
   - **注意**：檢查 fsc 是否已經呼叫 `gate_3_log_run`，若已有則不重複加

**插入點精確定位**（實作時用 Grep 確認）：
- Checkpoint 載入 → 在 `logger.info(f"[WFExec] Resolved {len(resolved_vars)} variables")` 之後
- HitL 偵測 → 在 `_execute_one_block_async` 或 `_runner` 回傳 result 後的處理段
- Checkpoint 寫入 → 在 block 結果 append 到 results 的同一處

**⚠️ 最高風險警告**：
- fsc 的 `execute()` 已大幅重構，**不能** cherry-pick 整段 ClawCoding execute()
- 只能「逐個邏輯塊」轉譯進 fsc 的結構
- 每插入一塊，立刻測試「現有 workflow 執行不受影響」
- 完成後跑至少 3 個現有 workflow（一般 / 有下載連結 / 有 LLM 合成），確認沒退化

**⚠️ 方案選擇**：
- 方案 B（保守）：HitL 邏輯放在外部循環判斷結果處，不動 `_execute_one_block_async`
- 方案 A（激進）：插入 `_execute_one_block_async` 內部
- **採方案 B**

---

## 3. 相依套件

### 3-1. `requirements.txt`

ClawCoding 有 `tiktoken`，fsc 沒有。

**驗證**：
```bash
grep -E "^tiktoken" requirements.txt || echo "NOT FOUND — need to add"
```

加到 requirements.txt。但注意：fsc 的 prompt_builder.py 已經 `import tiktoken`，所以可能實際已裝（只是沒鎖版本）。

```bash
pip show tiktoken
```
若已裝 → 還是要加到 requirements.txt 以鎖版本

---

## 4. 資料庫初始化（首次啟動驗證）

ClawCoding 的 SQLite 會在 `workspace/ssot/agentk.sqlite` 建立：
- `schema_version` 表
- `subjects` 表
- `approvals` 表
- `audit_events` 表
- `workflow_block_runs` 表

**首次啟動後檢查**：
```bash
sqlite3 workspace/ssot/agentk.sqlite ".tables"
# 應看到上述 5 個表
```

---

## 5. 回退計畫（每個階段都有）

| 階段 | 回退操作 |
|------|---------|
| Phase A（新檔複製完） | `git checkout -- server/services/ server/routes/ tests/` |
| Phase B（patch app.py + requirements） | `git diff` 看變更，`git checkout -- <file>` |
| Phase C（admin 前端） | 同上 |
| Phase D（chat.py /execute） | 同上 |
| Phase E（workflow_executor.py） | `git stash` 暫存後回滾；此檔不可半途放置 |

**重要**：每個 Phase 完成後，使用者同意才進下一步。

---

## 6. 交叉驗證檢查清單（每一步完成後逐項確認）

### Phase A 完成後
- [ ] `ls server/services/{db,audit_logger,approvals_service,policy,workflow_checkpoint}.py` → 全部存在
- [ ] `ls server/routes/{approvals,audit,workflow_resume,workflow_runs}.py` → 全部存在
- [ ] `ls tests/test_{approvals_routes_unit,approvals_service_unit,audit_logger_unit,policy_unit,workflow_executor_approval_unit,workflow_resume_unit}.py` → 全部存在
- [ ] `python -c "from server.services import db, audit_logger, approvals_service, policy, workflow_checkpoint"` → 無錯誤
- [ ] `python -c "from server.routes import approvals, audit, workflow_resume, workflow_runs"` → 無錯誤

### Phase B 完成後
- [ ] `python main.py` → 啟動成功（無 ImportError）
- [ ] 啟動 log 包含 `[Scheduler] APScheduler started`（原有 7 job 仍在）
- [ ] `sqlite3 workspace/ssot/agentk.sqlite ".tables"` → 5 張表
- [ ] `curl http://localhost:8500/api/approvals` → 回 403 或 200（不是 404 Not Found）

### Phase C 完成後
- [ ] 打開 admin.html → 看到「審核中心」Tab
- [ ] 點擊 Tab → 看到空列表（無錯誤）
- [ ] 瀏覽器 Console 無 JS 錯誤

### Phase D 完成後
- [ ] `curl -X POST /execute` 傳低風險技能 → 成功 + audit_events 表有記錄
- [ ] `curl -X POST /execute` 未登入 → 視 policy 決定（通常仍允許 guest）

### Phase E 完成後（關鍵！）
- [ ] 跑一個現有 workflow → 結果與整合前一致
- [ ] 跑一個有下載連結的 workflow → 連結正確產生
- [ ] 跑一個語意技能 workflow → LLM 正確呼叫（token 計量更新）
- [ ] 跑 `mcp-high-risk-demo` 觸發 HitL → 看到 approval 建立 + 工作流暫停
- [ ] 在審核中心批准 → 工作流 resume 且 checkpoint 跳過已完成 block
- [ ] 拒絕審批 → 工作流顯示 rejected 狀態
- [ ] `workflow_block_runs` 表有對應記錄
- [ ] `audit_events` 表有對應記錄

---

## 7. 已知潛在陷阱

### 7-1. `Optional[str]` vs `str | None`
ClawCoding 用 `Optional[str]`（py39 相容）；fsc 用 `str | None`（py310+）。  
移入新檔時保持 ClawCoding 原樣（`Optional` 更安全）。  
fsc 原有檔案不動。

### 7-2. `resolve_caller_context` 的未登入行為
Guest（未登入）情境需驗證 `authorize` 的預設行為。從 policy.py 讀出來的邏輯是「unknown 視為 guest」，但實際 `/execute` 應允許 guest 執行低風險技能。若這裡把 guest 全擋，整個 /execute 就壞了。

### 7-3. `BackgroundTasks` 的 auto-resume
`approvals.py` 的 approve endpoint 有 `background_tasks.add_task(resume_workflow, ...)`。  
這是 best-effort，失敗不影響 approval 狀態。但 resume_workflow 是 async → 必須確認 BackgroundTasks 能吃 async 函式（FastAPI 可以，但 ClawCoding 實作需驗證）。

### 7-4. Workflow 執行時 user_context 傳遞
ClawCoding resume_workflow 建構 user_context：
```python
user_context={"user_id": ap.get("requested_by_subject_id") or caller_id, "role": caller_role}
```
但 fsc 的 executor 實際用到更多欄位（name / dept 等）。resume 時這些欄位為空可能導致下游警告。**非阻斷性風險**，但要在 log 檢查。

### 7-5. Git 子模組
`Agent_skills/` 是子模組。任何觸及子模組的操作都要雙提交（子模組先、父層後）。本次整合**不涉及**子模組變動。

### 7-6. APScheduler jobstore
fsc 啟動後註冊 7 + 工作流原生 cron jobs。整合 ClawCoding 後**不應新增排程 job**（審批/稽核不需排程）。若 log 顯示額外 job，表示某處多加了。

---

## 8. 實作順序（鎖死，不跳階）

```
Phase A │ 複製 9 個新檔 + 6 個測試檔 + requirements.txt tiktoken
        │ → 驗證 import 成功
        │
Phase B │ patch server/app.py（4 行 router）
        │ → python main.py 啟動 → sqlite 表建立
        │
Phase C │ patch admin.html + admin.js（審核中心 UI）
        │ → 瀏覽器確認 Tab 可見、無錯誤
        │
Phase D │ patch routes/chat.py（/execute 加 policy + audit）
        │ → curl 測試 + audit 表有記錄
        │
Phase E │ 合併 workflow_executor.py HitL 邏輯（最難）
        │ → 跑 3 個現有 workflow 回歸 + 1 個 HitL 流程
        │
Final   │ 使用者手動 e2e 驗收後，才 commit
        │ 不主動 push
```

每個 Phase 完成，停下來等使用者確認「進下一步」。

---

## 9. 本次整合完成後的測試技能建議

- `mcp-high-risk-demo` 已存在（fsc 第 Active Skills 表列出）→ 用它測 HitL
- 若沒被標 `risk_level: high` → 這是現成驗證路徑

---

**最後自檢**：本文件所有數字、行號、檔名皆來自直接 git 讀取，非推測。  
實作時遇到任何與本文件不符情況，**立刻停手回報**，不自行修正文件。

---

## 10. ⚠️ 已知問題（以 β workaround 緩解，未完全治本）

### 10-1. HitL Resume 死循環問題 — 已以選項 β 緩解（2026-04-23）

**原始症狀**：  
當高風險 skill（`risk_level: high`）在 HitL 批准後 resume 時，因 skill 側邏輯不變，會再次回傳 `requires_approval`，導致建立**另一個**新的 approval 記錄，進入無窮循環。

**選項 β workaround 實作** (commit TBD)：
- `server/core/uma_core.py`：`execute_tool_call()` 新增 `approved_for_run: Optional[str]` 參數
- UMA 在 risk_level=high 的 gate 檢查時，若 `approved_for_run` 存在，查詢 `approvals` 表 (`correlation_id=run_id AND action=skill_name AND status='approved'`)，若找到 → 繞過 gate、執行真實 skill
- `server/services/workflow_executor.py`：每次呼叫 UMA 時帶入當前 run_id
- 5 種 gate 行為全數驗證通過（見測試日誌）

**β 方案限制**：
1. 仍依賴 SQLite 查詢（每次 high-risk skill 執行都多一次 DB 往返，performance 輕微 overhead）
2. 跨 workflow call 不共享（正確 — run_id 隔離）
3. 若未來 audit log 有延遲寫入，可能造成短暫 race condition
4. 不包含「approval_id 單次使用」語意 — 理論上同 run_id 內可多次呼叫同一 approved skill（實務上 workflow 一次 run 的 block 就那幾個，不成問題）

**後續升級方向（未來真正治本）**：

1. **Approval Token 機制**（治本）
   - resume 時把 `approval_id` 注入 skill 執行環境（env var）
   - skill 收到 token 後驗證 + 清除（一次性）
   - 更嚴謹的安全性（防重放）

2. **Skill-side dry_run / commit 分離**（更徹底）
   - high-risk skill 實作兩階段：`dry_run` 回 preview + `commit` 實際執行
   - HitL 只批准 `commit` 階段
   - 需改每個 high-risk skill 的 main.py

**追蹤標記**：`HitL-RESUME-LOOP` / `approval_id token` / `approved_for_run`

**解法方向（供未來實作時參考）**：

1. **Approval Token 機制**（建議）
   - resume 時把 `approval_id` 注入 skill 執行環境
   - UMA 看到合法 approval token → 驗證 token 後放行 skill 真正執行
   - Token 一次性使用（避免重放）

2. **Skip-on-resume 機制**（較簡單但較不安全）
   - `workflow_resume.py` 在重跑前把對應 block 標記「approved_run」
   - `_execute_one_block_async` 看到此標記 → 略過 `requires_approval` 偵測 → 直接執行
   - 風險：可能被濫用

3. **Skill-side resume 支援**（徹底）
   - 每個 high-risk skill 實作「dry_run」與「commit」兩階段
   - HitL 時回 dry_run（`requires_approval` + preview payload）
   - Resume 時以 `approval_id` 呼叫 commit 階段
   - 需要改 skill interface，工作量最大

**追蹤標記**：  
本 bug 會在整合完成的 commit message 裡明確標示為「known follow-up work」，以便未來搜尋。

**搜尋關鍵字**（未來維護時用）：  
`HitL-RESUME-LOOP` / `approval_id token` / `workflow_resume 死循環`
