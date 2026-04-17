# AgentK Skills & Workflow 分類存放機制 — 評估實作報告

> 報告日期：2026-04-10
> 撰寫人：范書愷 / Claude
> 狀態：評估完成，待決策

---

## 一、現有架構分析

### 1.1 Skill 系統現狀

**目錄結構：**
```
Agent_skills/                    ← Git submodule (fsc0638/Agent_skills, branch: main)
├── skills/                      ← SKILLS_HOME (.env: Agent_skills/skills)
│   ├── mcp-web-search/
│   │   ├── SKILL.md             ← YAML frontmatter + 指令
│   │   ├── SKILL.md.bak
│   │   ├── scripts/main.py
│   │   └── references/
│   ├── mcp-python-executor/
│   ├── mcp-google-calendar/
│   └── ... (共 16 個 skill)
├── shared/                      ← 共用資源 (stop_words.json)
├── skills_manifest.json         ← 自動生成的 SSOT 索引
└── .git/                        ← Git 根目錄
```

**載入流程：**
```
main.py → UMA(skills_home) → SkillRegistry.scan_skills()
  → 掃描 skills_home 下所有子目錄
  → 每個子目錄找 SKILL.md → _register_skill()
  → 以 目錄名.lower() 為 key 存入 self.skills: Dict
```

**關鍵限制：**
- `scan_skills()` 只掃描**單一目錄** (`skills_home`)
- `get_tools_for_model()` 回傳**所有 skill**，無使用者篩選
- `get_skill()` 用 `skill_name.lower()` 查詢，無命名空間概念
- Skill API（CRUD）路徑解析：`skills_home / skill_name`，扁平結構

### 1.2 Workflow 系統現狀

**目錄結構：**
```
workspace/workflows/
└── default.json                 ← 唯一的 workflow
```

**儲存格式：**
```json
{
  "id": "default",
  "name": "My Workflow",
  "blocks": [{"id": 1, "type": "start", "x": 200, "y": 250, ...}],
  "connections": [{"from": 1, "to": 2}],
  "created_at": "2026-04-09T...",
  "updated_at": "2026-04-10T..."
}
```

**關鍵限制：**
- 路徑模式：`workspace/workflows/{workflow_id}.json`（扁平）
- 無使用者歸屬、無部門分類
- 前端只操作 `default` 這一個 workflow
- 執行時透過 `WorkflowLLMRouter` + `UMA.execute_tool_call()` 串接

### 1.3 使用者系統現狀

**User Context 來源：**

| 來源 | 路徑 | 內容 |
|------|------|------|
| 員工清單 | `workspace/department/同仁清單.xlsx` | 148人, 21部門 |
| 使用者資料 | `workspace/users/{session_id}.json` | Onboarding 後建立 |
| 使用者檔案 | `workspace/profiles/{session_id}.profile.md` | AI 生成的 profile |
| Session 登入 | `sessionStorage('kway_user')` | 前端使用者狀態 |

**可用欄位：**
```json
{
  "user_id": "U09e3122dcc...",
  "name": "范書愷",
  "email": "fsc@kway.com.tw",
  "department_code": "A100",
  "department_name": "研發中心",
  "role": "editor",
  "skill_access": {
    "system": "all",
    "department": ["A100"],
    "personal": true
  }
}
```

**關鍵發現：** `skill_access` 結構已定義但**完全未執行**。目前所有使用者看到所有 skill。

### 1.4 影響範圍清單

現有程式碼中，以下位置需要配合修改：

| 檔案 | 函式/位置 | 影響 |
|------|----------|------|
| `server/core/uma_core.py` | `SkillRegistry.scan_skills()` | 需支援多目錄掃描 |
| `server/core/uma_core.py` | `get_tools_for_model()` | 需加入使用者篩選 |
| `server/core/uma_core.py` | `get_skill()` | 需支援命名空間查找 |
| `server/core/uma_core.py` | `execute_tool_call()` | 需支援多路徑 skill 執行 |
| `server/routes/skills.py` | 所有 CRUD 端點 | 路徑解析需加入層級 |
| `server/routes/skills.py` | `sync_skills_git()` | git root 已修正 |
| `server/routes/workflow.py` | `_workflows_dir()` | 需改為分層目錄 |
| `server/routes/workflow.py` | 所有 CRUD 端點 | 路徑需含 scope/owner |
| `server/services/chat_core.py` | `process_chat_native()` | 需傳入 user context |
| `server/adapters/__init__.py` | `select_relevant_tools()` | 需加入權限篩選 |
| `server/integrations/line_connector.py` | 工具注入 | 需傳入 session user context |
| `frontend/assets/js/workflow.js` | Skill 列表載入 | 需帶入 scope 參數 |
| `frontend/assets/js/workflow.js` | SkillEditor 儲存/建立 | 需帶入歸屬層級 |
| `frontend/assets/js/workflow.js` | Workflow 儲存/載入 | 需帶入 user scope |
| `.env` | `SKILLS_HOME` | 可能需擴充為多路徑 |

---

## 二、目標架構設計

### 2.1 Skill 三層架構

```
Agent_skills/
├── skills/                          ← 系統級（所有人可用）
│   ├── mcp-web-search/
│   ├── mcp-python-executor/
│   └── ...
├── department_skills/               ← 部門級（該部門可用）
│   ├── A100/                        ← 研發中心
│   │   ├── mcp-code-review/
│   │   └── mcp-deploy-checker/
│   ├── B200/                        ← 金融事業處
│   │   └── mcp-risk-analyzer/
│   └── ...
└── user_skills/                     ← 個人級（僅建立者可用）
    ├── U09e3122dcc/
    │   └── mcp-my-custom-tool/
    └── ...
```

**命名空間規則：**
- 系統級：`system:{skill_name}` → 路徑 `skills/{skill_name}`
- 部門級：`dept:{dept_code}:{skill_name}` → 路徑 `department_skills/{dept_code}/{skill_name}`
- 個人級：`user:{user_id}:{skill_name}` → 路徑 `user_skills/{user_id}/{skill_name}`
- **相容性**：API 層面 skill_name 仍可直接使用（優先查詢順序：系統 → 部門 → 個人）

### 2.2 Workflow 分層架構

```
workspace/workflows/
├── system/                          ← 系統範本（admin 管理）
│   ├── news-analysis.json
│   └── weekly-report.json
├── department/                      ← 部門共用
│   ├── A100/
│   │   └── sprint-review.json
│   └── B200/
│       └── market-scan.json
└── personal/                        ← 個人
    ├── U09e3122dcc/
    │   ├── my-workflow.json
    │   └── test-flow.json
    └── ...
```

### 2.3 存取權限矩陣

| 操作 | system skill | dept skill (自己部門) | dept skill (他部門) | personal skill (自己) | personal skill (他人) |
|------|:-----------:|:-------------------:|:------------------:|:-------------------:|:-------------------:|
| **可見/使用** | 所有人 | 該部門成員 | ✗ | 僅本人 | ✗ |
| **建立** | admin | editor | ✗ | editor | ✗ |
| **編輯** | admin | editor (同部門) | ✗ | 僅本人 | ✗ |
| **刪除** | admin | editor (同部門) | ✗ | 僅本人 | ✗ |

Workflow 權限矩陣相同。

### 2.4 Skill 可見性篩選流程

```
使用者發送訊息
  ↓
取得 user_context（department_code, user_id, role）
  ↓
get_tools_for_model(model_type, user_context)    ← 新增參數
  ↓
┌─ 系統 skills/  → 全部加入
├─ department_skills/{user.dept_code}/  → 加入
└─ user_skills/{user.user_id}/  → 加入
  ↓
select_relevant_tools(query, filtered_tools, max_tools)
  ↓
回傳給 LLM
```

---

## 三、實作計畫

### Phase 1 — 目錄結構 + 多路徑掃描（不動權限）

**目標**：讓系統能掃描三層 skill 目錄，但暫不做權限過濾（所有人仍看到所有 skill）。

| 項目 | 檔案 | 變更 |
|------|------|------|
| 1-1 | `.env` | 新增 `DEPT_SKILLS_HOME`, `USER_SKILLS_HOME` |
| 1-2 | `main.py` | 讀取新環境變數，傳入 UMA |
| 1-3 | `uma_core.py` | `SkillRegistry.__init__()` 接受多路徑；`scan_skills()` 掃描三層 |
| 1-4 | `uma_core.py` | `_register_skill()` 加入 `scope` 標籤（system/dept/user） |
| 1-5 | `uma_core.py` | `get_skill()` 支援命名空間查找（優先序：system → dept → user） |
| 1-6 | `skills.py` | 各 API endpoint 的路徑解析支援 `scope` 參數 |
| 1-7 | `skills.py` | `list_skills()` 回傳結果加入 `scope` 欄位 |

**預估工作量**：2-3 天

### Phase 2 — 權限篩選

**目標**：根據使用者的 department_code 和 user_id，篩選可見的 skill 和 workflow。

| 項目 | 檔案 | 變更 |
|------|------|------|
| 2-1 | `uma_core.py` | `get_tools_for_model()` 新增 `user_context` 參數 |
| 2-2 | `uma_core.py` | 根據 scope + user context 過濾 skill 列表 |
| 2-3 | `uma_core.py` | `execute_tool_call()` 加入執行權限檢查 |
| 2-4 | `chat_core.py` | 傳入 session 的 user context 到 adapter |
| 2-5 | `__init__.py` (adapters) | `select_relevant_tools()` 加入權限前篩選 |
| 2-6 | `line_connector.py` | 從 session metadata 取 user context 傳入 |
| 2-7 | `skills.py` | CRUD endpoint 加入權限檢查（edit/delete 需 owner 驗證） |

**預估工作量**：3-4 天

### Phase 3 — Workflow 分層 + 前端 UI

**目標**：Workflow 支援 system/dept/personal 分類存放 + 前端支援。

| 項目 | 檔案 | 變更 |
|------|------|------|
| 3-1 | `workflow.py` | `_workflows_dir()` 支援分層路徑 |
| 3-2 | `workflow.py` | CRUD API 加入 `scope` 和 `owner` 參數 |
| 3-3 | `workflow.py` | `list_workflows()` 根據 user context 過濾 |
| 3-4 | `workflow.js` | 前端 Skill 列表載入帶 scope 參數 |
| 3-5 | `workflow.js` | SkillEditor 建立/儲存加入層級選擇（系統/部門/個人） |
| 3-6 | `workflow.js` | Workflow 儲存/載入加入 scope 路由 |
| 3-7 | `chat.html` | Skill 列表加入分類標頭（系統/部門/個人） |

**預估工作量**：3-4 天

### Phase 4 — 管理介面 + Git 整合

**目標**：admin 管理介面 + 多層 Git 同步策略。

| 項目 | 檔案 | 變更 |
|------|------|------|
| 4-1 | `skills.py` | `sync_skills_git()` 支援 dept/user 目錄的 commit |
| 4-2 | 新增 | Admin 頁面：部門管理、使用者角色管理 |
| 4-3 | 新增 | Skill 搬移功能（個人 → 部門 → 系統 升級路徑） |
| 4-4 | 新增 | Workflow 範本複製功能（system 範本 → 個人副本） |

**預估工作量**：4-5 天

---

## 四、技術設計細節

### 4.1 SkillRegistry 多路徑掃描

```python
class SkillRegistry:
    def __init__(self, skills_home, dept_skills_home=None, user_skills_home=None):
        self.skills_home = Path(skills_home)          # Agent_skills/skills/
        self.dept_skills_home = Path(dept_skills_home) if dept_skills_home else None
        self.user_skills_home = Path(user_skills_home) if user_skills_home else None
        self.skills: Dict[str, Dict] = {}

    def scan_skills(self):
        # 1. 系統 skills
        self._scan_directory(self.skills_home, scope="system")
        # 2. 部門 skills（二層結構：dept_code/skill_name）
        if self.dept_skills_home and self.dept_skills_home.exists():
            for dept_dir in self.dept_skills_home.iterdir():
                if dept_dir.is_dir():
                    self._scan_directory(dept_dir, scope=f"dept:{dept_dir.name}")
        # 3. 個人 skills（二層結構：user_id/skill_name）
        if self.user_skills_home and self.user_skills_home.exists():
            for user_dir in self.user_skills_home.iterdir():
                if user_dir.is_dir():
                    self._scan_directory(user_dir, scope=f"user:{user_dir.name}")
```

### 4.2 Skill 可見性篩選

```python
def get_tools_for_model(self, model_type, user_context=None):
    tools = []
    for name, skill in self.skills.items():
        scope = skill["metadata"].get("_scope", "system")
        
        # 無 user context → 只回傳系統 skill（安全預設）
        if user_context is None:
            if scope != "system":
                continue
        else:
            # 權限檢查
            if scope.startswith("dept:"):
                dept_code = scope.split(":")[1]
                if dept_code != user_context.get("department_code"):
                    continue
            elif scope.startswith("user:"):
                owner_id = scope.split(":")[1]
                if owner_id != user_context.get("user_id"):
                    continue
        
        # 加入 tool 列表
        tools.append(self._format_tool(name, skill, model_type))
    return tools
```

### 4.3 Skill 名稱衝突處理

不同層級可能有同名 skill（如 system 和 dept 都有 `mcp-report-gen`）：

**策略：內部 key 加 scope prefix**
```python
# 註冊時
key = f"{scope}:{skill_name}"  # e.g. "system:mcp-web-search", "dept:A100:mcp-custom"

# 查詢時（向下相容）
def get_skill(self, skill_name, user_context=None):
    # 1. 精確匹配（帶 scope prefix）
    if skill_name in self.skills:
        return self.skills[skill_name]
    # 2. 短名匹配（優先序：system → user's dept → user's personal）
    for scope in ["system", f"dept:{user_context['dept']}", f"user:{user_context['user_id']}"]:
        key = f"{scope}:{skill_name}"
        if key in self.skills:
            return self.skills[key]
    return None
```

### 4.4 Workflow 路徑路由

```python
def _workflows_dir(scope="personal", owner=None):
    base = Path(os.getenv("PROJECT_ROOT", ".")) / "workspace" / "workflows"
    if scope == "system":
        return base / "system"
    elif scope == "department":
        return base / "department" / (owner or "unknown")
    else:  # personal
        return base / "personal" / (owner or "unknown")
```

### 4.5 前端 Skill 列表分類顯示

```
┌─────────────────────┐
│ 📌 系統技能           │
│  🔍 網路搜尋          │
│  🐍 Python 執行       │
│  📊 試算表分析         │
│  ...                  │
├─────────────────────┤
│ 🏢 部門技能（研發中心）  │
│  🔧 Code Review       │
│  🚀 Deploy Checker    │
├─────────────────────┤
│ 👤 個人技能            │
│  📝 My Custom Tool    │
│  + 新增個人 Skill     │
└─────────────────────┘
```

---

## 五、向下相容策略

### 5.1 不動的東西

| 項目 | 說明 |
|------|------|
| SKILL.md 格式 | YAML frontmatter + markdown，不變 |
| 執行模式 | executable/code/semantic 三種，不變 |
| Two-phase 注入 | Phase 1 輕量列表 → Phase 2 按需注入，不變 |
| subprocess 執行 | ExecutionEngine.run_script()，不變 |
| LLM 參數路由 | WorkflowLLMRouter，不變 |
| LINE Bot 對話 | session_id 格式不變，工具注入方式不變 |

### 5.2 遷移策略

```
Phase 1 完成後：
- 現有 skills/ 下的 16 個 skill → 自動歸類為 system scope
- department_skills/ 和 user_skills/ 目錄為空 → 不影響現有功能
- 所有 API 向下相容（不帶 scope 參數 = system）

Phase 2 完成後：
- 無 user context 的 API 呼叫 → 只看到 system skill（安全預設）
- LINE Bot 對話 → 根據 session user context 自動篩選
- Web UI → 根據登入使用者自動篩選

Phase 3 完成後：
- Workflow default.json → 遷移至 personal/{user_id}/default.json
- Skill Editor 新增層級選擇器
```

---

## 六、風險評估

| 風險 | 影響 | 緩解措施 |
|------|------|---------|
| Skill 名稱衝突 | 不同層級同名 skill 混淆 | 內部 key 加 scope prefix，查詢有優先序 |
| Git 同步複雜度 | dept/user 目錄的 commit 訊息歸屬 | 統一走 sync_skills_git()，已含 user info |
| 效能：掃描量增大 | 三層掃描啟動時間增加 | 增量掃描（hash-based delta），已有機制 |
| 未登入使用者 | 無 user context 的請求 | 安全預設：只回傳 system skill |
| LINE 群組 | 群組內多人共用 session | 群組 → 對應部門的 skill，而非個人 skill |
| 個人 skill 儲存量 | 每個人可能建很多 skill | 設上限（如每人 20 個），disk quota |

---

## 七、建議實作順序

```
Week 1 — Phase 1：多路徑掃描（不動權限）
  ├── 建目錄結構 + .env 設定
  ├── SkillRegistry 多路徑支援
  ├── Skill API scope 參數
  └── 驗證：現有 16 個 skill 正常運作

Week 2 — Phase 2：權限篩選
  ├── get_tools_for_model() 加 user context
  ├── chat_core / adapter 傳入 user context
  ├── CRUD 權限檢查
  └── 驗證：不同使用者看到不同 skill

Week 3 — Phase 3：Workflow 分層 + 前端
  ├── Workflow 路徑分層
  ├── 前端 scope 選擇器
  ├── Skill 列表分類顯示
  └── 驗證：建立/使用個人 workflow + dept skill

Week 4 — Phase 4：管理 + 進階
  ├── Admin 介面
  ├── Skill 升級路徑（個人→部門→系統）
  └── Workflow 範本系統
```

---

## 八、結論

現有架構已預留 `skill_access` 結構但未實施，改動的核心在於：

1. **SkillRegistry 多路徑掃描** — 最關鍵的基礎設施變更，讓系統能感知三層 skill
2. **get_tools_for_model() 加入 user_context** — 唯一的「行為改變」，從「全開」變「依權限開」
3. **Workflow 路徑分層** — 相對獨立，可平行開發

最小可行版本（MVP）= Phase 1 + Phase 2，約 1.5~2 週。Phase 3/4 可在 MVP 穩定後逐步推進。
