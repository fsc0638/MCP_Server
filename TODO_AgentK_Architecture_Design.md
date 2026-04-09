# TODO: AgentK Skills & Workflow 架構設計

> 建立日期：2026-04-09
> 狀態：規劃中

---

## Phase 1 — Workflow 後端存儲 + 執行引擎

### Workflow 存儲
- [ ] 建立 `workspace/workflows/` 目錄結構
  - [ ] `system/` — 系統範本 Flow
  - [ ] `department/{dept}/` — 部門共用 Flow
  - [ ] `personal/{user_id}/` — 個人 Flow
- [ ] Workflow JSON 格式定義：blocks, connections, trigger, context, metadata
- [ ] API: `GET/POST/PUT/DELETE /api/workflows/{id}`
- [ ] 前端 Save/Load 從 localStorage 改接 API

### Workflow 執行引擎
- [ ] 建立 `server/services/workflow_engine.py`
- [ ] 執行流程：
  1. 讀取 Workflow JSON
  2. Topological sort blocks
  3. 依序執行每個 block（呼叫 UMA.execute_tool_call）
  4. 前一步 output → 下一步 input（LLM context 串接）
  5. End block → 組裝最終結果
- [ ] 與 ScheduledPushService 整合（排程觸發 Workflow）
- [ ] 執行結果記錄到 `workspace/workflows/logs/`

---

## Phase 2 — User Context + Skill 分層

### User Context 結構
- [ ] 定義 User Context JSON：
  ```json
  {
    "user_id": "U09e3122dcc...",
    "name": "范書愷",
    "email": "fsc@kway.com.tw",
    "department": "dev",
    "role": "admin",
    "groups": ["line_group_Cf8ce..."],
    "skill_access": {
      "system": "all",
      "department": ["dev"],
      "personal": true
    }
  }
  ```
- [ ] 存儲位置：`workspace/users/{user_id}.json` 或整合到 profiles/
- [ ] LINE user_id ↔ department 對應機制
- [ ] LINE group_id ↔ department 對應機制

### Skill 三層架構
- [ ] 目錄結構：
  ```
  Agent_skills/
  ├── skills/                  ← 系統級（所有人可用）
  ├── department_skills/{dept}/ ← 部門級（該部門可用）
  └── user_skills/{user_id}/    ← 個人級（僅建立者可用）
  ```
- [ ] 修改 `SkillRegistry.scan_skills()` 支援多層掃描
- [ ] 修改 `get_tools_for_model()` 根據 User Context 篩選可見 Skill
- [ ] Skill Editor 顯示歸屬層級 + 允許切換

---

## Phase 3 — 權限控制

### 角色定義
- [ ] admin — 可管理所有 Skill / Workflow / 用戶
- [ ] editor — 可建立/修改自己的 Skill / Workflow
- [ ] viewer — 只能使用，不能修改

### 權限檢查點
- [ ] Skill CRUD API 加權限檢查
- [ ] Workflow CRUD API 加權限檢查
- [ ] Skill Editor UI 根據角色顯示/隱藏編輯功能
- [ ] LLM tool injection 根據 user context 篩選

### 部門群組管理
- [ ] 管理介面：部門 CRUD
- [ ] 用戶歸屬部門設定
- [ ] LINE 群組 ↔ 部門綁定

---

## Phase 4 — 進階功能

### 錯誤 Callback Flow
- [ ] Flow 失敗時觸發指定的備用 Flow
- [ ] 傳遞系統變數（job_name, timestamp, error info）
- [ ] 傳遞原 flow 的累積變數

### 平行分支
- [ ] 真正的 parallel execution（多個 block 同時執行）
- [ ] 合併節點等待所有分支完成
- [ ] 分支間的變數隔離

### Workflow 版本管理
- [ ] Flow 存檔帶版本號
- [ ] 支援回滾到歷史版本
- [ ] 修改記錄追蹤

---

## 核心設計原則

1. **LLM-First** — AgentK 的 Workflow 不是傳統 DAG，每個 Block 的 output 由 LLM 理解並傳給下一個 Block
2. **漸進式** — 先讓 Workflow 能存+能跑（Phase 1），再加分層+權限（Phase 2-3）
3. **向下相容** — 現有 LINE Bot / Web UI 對話模式不受影響，Workflow 是另一種觸發方式
4. **Skill 是原子單位** — 不管對話觸發還是 Workflow 觸發，Skill 執行方式完全一樣（UMA.execute_tool_call）

---

## 相關文件

- `EVALUATION_Google_Cloud_NL_API.md` — NL API 導入評估
- `TODO_MAGELLAN_BLOCKS_Integration.md` — MAGELLAN BLOCKS 對接評估
- `IMPLEMENTATION_REPORT_Google_Workspace_and_Training.md` — Google Workspace 整合報告
