# MCP Server 程式架構重構建議報告 v3

> 基於 2026-03-12 實際程式碼全面檢視，完全重新規劃
> 分支：`poyuan` | AgentPortal 已不存在，以 `static/` 為唯一前端基準

---

## 目錄

1. [現況全面盤點](#1-現況全面盤點)
2. [發現的問題清單](#2-發現的問題清單)
3. [實際依賴關係圖](#3-實際依賴關係圖)
4. [建議新架構](#4-建議新架構)
5. [分階段執行計畫](#5-分階段執行計畫)
6. [風險與注意事項](#7-風險與注意事項)

---

## 1. 現況全面盤點

### 1.1 根目錄現況（實際掃描）

```
MCP_Server/
├── .env                          ← 執行中的設定（含 API keys）
├── .env.template                 ← 設定範本
├── .git/                         ← 主 git 倉庫
├── .github/                      ← GitHub workflows
├── .gitignore
├── .gitmodules                   ← git 子模組設定
│
├── [業務邏輯 - 散落根目錄]
│   ├── main.py           95 行   ← UMA 初始化入口
│   ├── router.py      1,803 行   ← FastAPI 主路由（過度肥大）
│   ├── line_connector.py 295 行  ← LINE API 整合
│   └── auto_agent.py    319 行   ← CLI 自動代理
│
├── [開發/測試 - 散落根目錄]
│   ├── test_phase1.py            ← 測試腳本
│   ├── uma_demo.py               ← UMA 示範腳本
│   └── start_server.bat          ← Windows 啟動腳本
│
├── [架構文件 - 散落根目錄]
│   ├── AutoScan_MCP_Migration_Plan.md
│   ├── Flexibility in open language model...md
│   ├── LINE_Integration_Cross_Analysis_Report.md
│   ├── MCP_Architecture_Report.md
│   ├── MCP_Full_Architecture_Report.md
│   ├── NotebookLM_Architecture_Evolution_Plan.md
│   ├── Sprint1-4_Architecture_Report.md
│   ├── Sprint2/3/4_Implementation_Plan.md
│   ├── Sprint_1_Implementation_Plan.md
│   └── UMA_Implementation_Report.md（共 11 份）
│
├── core/                         ← 核心業務邏輯（結構良好）
├── adapters/                     ← LLM 適配器（結構良好）
├── static/                       ← 唯一前端（單頁應用）
├── Agent_skills/                 ← 技能倉庫（有獨立 .git）
├── scripts/                      ← 工具腳本
├── memory/                       ← Session 持久化
├── workspace/                    ← 使用者上傳文件
├── report/                       ← 架構報告（未追蹤）
├── skills_backup/                ← 廢棄備份
└── tmp/                          ← 暫存目錄
```

### 1.2 各層詳細盤點

#### 後端層

| 檔案 | 大小 | 行數 | 主要職責 |
|------|------|------|---------|
| `main.py` | 2.8 KB | 95 | UMA 初始化、`get_uma()` 暴露 |
| `router.py` | 76.7 KB | 1,803 | 路由、Session、RAG、技能 CRUD、文件管理、LINE 掛載 |
| `line_connector.py` | 11.9 KB | 295 | LINE Webhook、訊息處理、會話路由 |
| `auto_agent.py` | 12.3 KB | 319 | CLI 多輪對話代理 |

#### 核心層（`core/`）—— 架構良好

| 檔案 | 大小 | 職責 |
|------|------|------|
| `uma_core.py` | 12.6 KB | UMA 主類、SkillRegistry、ExecutionEngine 協調 |
| `session.py` | 13.9 KB | 對話歷史、自適應壓縮（40 訊息觸發） |
| `retriever.py` | 19.1 KB | FAISS 向量搜尋、多格式文件攝入 |
| `executor.py` | 7.8 KB | 技能腳本執行、路徑安全防護 |
| `converter.py` | 3.9 KB | 技能 schema → OpenAI/Gemini/Claude 轉換 |
| `watcher.py` | 6.9 KB | watchdog 監控 workspace/ 自動索引 |

#### 適配器層（`adapters/`）—— 架構良好

| 檔案 | 大小 | 職責 |
|------|------|------|
| `__init__.py` | 8.1 KB | 多語系 tokenization、動態工具注入 ⚠️ |
| `openai_adapter.py` | 14.4 KB | GPT 整合、Vision、Streaming |
| `gemini_adapter.py` | 19.9 KB | Gemini 整合、Function Calling |
| `claude_adapter.py` | 15.8 KB | Claude 整合、Tool Use |

#### 前端層（`static/`）—— 單頁應用，過度集中

| 檔案 | 大小 | 職責 |
|------|------|------|
| `index.html` | 48.1 KB | 三欄佈局、所有 UI 元素 |
| `app.js` | 104.3 KB | Module A（Chat）+ Module B（Skills）混合 |
| `i18n.js` | 40.5 KB | 繁中/英/日/韓 四語系 |
| `style.css` | 56.3 KB | K WAY 品牌設計系統 |

#### 技能倉庫（`Agent_skills/`）—— 有子模組複雜性

```
Agent_skills/
├── .git/                         ← 獨立 git 倉庫（子模組）
├── README.md
├── .env.template
├── skills_manifest.json  12 KB   ← 技能清單索引
├── scripts/
│   └── generate_manifest.py      ← 清單生成工具
├── shared/
│   ├── stop_words.json    4.7 KB
│   └── streaming_utils.py 1.3 KB
└── skills/                       ← 21 個技能模組
    ├── mcp-algorithmic-art/
    ├── mcp-brand-guidelines/
    └── ... (共 21 個)
```

### 1.3 設定不一致點（實際發現）

| 設定項目 | `.env.template` | `docker-compose.yml` | 問題 |
|---------|----------------|---------------------|------|
| 技能根目錄 | `SKILLS_HOME=./skills` | `./Agent_skills:/app/Agent_skills` | ⛔ **路徑不一致** |
| 主程式入口 | — | `uvicorn router:app` | `main.py` 與 `router.py` 各自是入口？ |

---

## 2. 發現的問題清單

### 🔴 高嚴重度

#### 問題 A：`router.py` 嚴重過載（1,803 行 / 76.7 KB）

`router.py` 目前承擔 **6 個不同領域**的職責，且全部混在同一個檔案：

```
router.py 目前管理的路由群組：
├── /api/models                     ← 模型清單
├── /api/documents/upload           ← 文件上傳（含 SHA-256 去重）
├── /api/documents/url              ← URL 爬取 + Markdown 轉換
├── /api/research                   ← Google Custom Search 整合
├── /chat                           ← LLM 對話（含 Session、RAG、Streaming）
├── /chat/flush/{session_id}        ← Session 管理
├── /skills/rescan                  ← 技能重掃描
├── /skills/create                  ← 技能建立
├── /skills/{name}/files            ← 技能檔案管理
├── /skills/list                    ← 技能列表 CRUD
└── /api/line/webhook               ← LINE 整合掛載點
```

**衝擊：** 合併衝突風險極高，任何功能修改需在 1,800 行中定位，新成員上手困難。

#### 問題 B：`line_connector.py` 緊耦合根目錄私有變數

```python
# line_connector.py 的實際 import（問題所在）：
from main import get_uma           # ← 依賴啟動入口的全域函數
from router import _session_mgr   # ← 依賴私有變數（底線前綴命名）
```

`_session_mgr` 是 `router.py` 的模組級私有變數，直接 import 私有變數表示兩個模組高度緊耦合。任何對 `router.py` 的重構都會立即 break `line_connector.py`。

#### 問題 C：`.env.template` 與 `docker-compose.yml` 路徑不一致

```bash
# .env.template 寫的：
SKILLS_HOME=./skills

# docker-compose.yml volume 掛載的：
./Agent_skills:/app/Agent_skills

# uma_core.py 讀取的：
os.getenv("SKILLS_HOME", "Agent_skills/skills")
```

三處不一致，Docker 啟動後技能路徑可能錯誤導致無技能可載入。

#### 問題 D：前端 `app.js` 過度集中（104 KB）

`app.js` 與 `router.py` 有相同問題：單一檔案承載過多邏輯。

```
app.js 目前包含（估計）：
├── Module A: CHAT（LLM 對話、Session、Streaming 顯示）
├── Module B: SKILLS（技能清單、技能 CRUD、抽屜 UI）
├── 文件上傳管理（進度條、SHA 去重）
├── Workspace 檔案預覽
├── 模型選擇器
└── UI 工具函數（Modal、Alert、Toast）
```

---

### 🟡 中嚴重度

#### 問題 E：`adapters/__init__.py` 職責錯位

```python
# adapters/__init__.py 包含（實際）：
def extract_tags(text):             # 多語系 tokenization（jieba、日文 regex、英文）
def select_relevant_tools(query):   # 動態工具注入邏輯
# + stop words 載入
# + 同義詞映射
```

這些是**NLP 語言處理邏輯**，與「Adapter 初始化」無關，放在 `__init__.py` 使得此模組難以獨立測試。

#### 問題 F：`Agent_skills/` 是獨立 git 倉庫

`Agent_skills/` 內含 `.git` 目錄，加上根目錄有 `.gitmodules`，表示這是 **git submodule** 配置。這意味著：
- 技能倉庫有獨立的版本控制週期
- 主倉庫的 `git pull` 不會自動更新技能
- 任何路徑調整需同步更新 `.gitmodules`

#### 問題 G：開發/測試/文件檔案散落根目錄

根目錄有 **11 份 `.md` 架構文件** + `test_phase1.py` + `uma_demo.py` + `start_server.bat`，使根目錄語意不清。

#### 問題 H：`skills_backup/` 廢棄資源仍存在

`skills_backup/` 含 20+ 舊技能目錄，佔用空間且容易造成混淆。

---

### 🟢 低嚴重度 / 優化項目

#### 問題 I：`static/style.css` 缺少深色模式

`style.css` 已有完整的 CSS 設計 Token 系統（K WAY 品牌色、radius、shadow），但尚未實作 `prefers-color-scheme: dark`，與 Apple 設計標準有落差。

#### 問題 J：前端為單頁架構，缺少登入/設定頁

目前 `static/index.html` 直接進入三欄聊天介面，無獨立登入頁，使用者體驗與 Apple 設計原則（漸進式揭露）有差距。

---

## 3. 實際依賴關係圖

### 現況（問題依賴）

```
┌─────────────────────────────────────────────────────────┐
│                   ROOT LEVEL（問題所在）                  │
│                                                          │
│  main.py ────── def get_uma() ──────────────────────┐   │
│  （95 行）      暴露給外部 import                    │   │
│                                                     │   │
│  router.py ─── _session_mgr（私有變數）─────────┐  │   │
│  （1,803 行）   承擔 6 個路由域                  │  │   │
│                初始化所有 adapters/session/retriever│  │   │
│                                                  │  │   │
│  line_connector.py ── from main import ──────────┘──┘   │
│  （295 行）      ──── from router import ─────┘          │
│                  LINE SDK 初始化                          │
│                                                          │
│  auto_agent.py ── 有 ADAPTER_FACTORY dict ✓              │
│  （319 行）       但仍各自初始化 adapter                   │
└─────────────────────────────────────────────────────────┘
         │                       │
         ↓                       ↓
      core/（✅ 結構良好）     adapters/（⚠️ __init__ 職責混雜）
      ├── uma_core.py          ├── __init__.py（含 NLP 邏輯）
      ├── session.py           ├── openai_adapter.py
      ├── retriever.py         ├── gemini_adapter.py
      ├── converter.py         └── claude_adapter.py
      ├── executor.py
      └── watcher.py
                    │
                    ↓
              Agent_skills/（獨立 .git 子模組）
              └── skills/（21 個技能）
```

### 目標（清晰依賴方向）

```
                 ┌──────────────────┐
                 │  server/app.py   │  ← 單一應用入口
                 └────────┬─────────┘
                          │ 掛載 APIRouter
         ┌────────────────┼─────────────────┐
         ↓                ↓                 ↓
  routes/chat.py   routes/skills.py  routes/documents.py
         │                │                 │
         └────────────────┴─────────────────┘
                          │ 注入依賴
                 ┌────────┴────────┐
                 │ dependencies/   │  ← FastAPI Depends
                 │ ├── uma.py      │
                 │ ├── session.py  │
                 │ └── retriever.py│
                 └────────┬────────┘
                          │
         ┌────────────────┼─────────────────┐
         ↓                ↓                 ↓
      core/            adapters/        integrations/
   （業務邏輯）         ├── factory.py    └── line_connector.py
                       └── *_adapter.py      (用 Depends 注入)
                              ↑
                           nlp/
                    （tokenizer + tool_selector）
```

---

## 4. 建議新架構

### 4.1 完整資料夾結構

```
MCP_Server/
│
├── server/                              ← 後端統一目錄（主要重構）
│   ├── app.py                           ← 應用入口（main.py + router 掛載整合）
│   │
│   ├── routes/                          ← router.py 拆分（各 ~200-400 行）
│   │   ├── __init__.py
│   │   ├── chat.py                      ← /chat/* （對話、Session、Streaming）
│   │   ├── documents.py                 ← /api/documents/*、/api/research
│   │   ├── skills.py                    ← /skills/*（CRUD、rescan、建立）
│   │   └── models.py                    ← /api/models、/health
│   │
│   ├── core/                            ← 現有 core/ 移入（不改內容）
│   │   ├── uma_core.py
│   │   ├── session.py
│   │   ├── retriever.py
│   │   ├── executor.py
│   │   ├── converter.py
│   │   └── watcher.py
│   │
│   ├── adapters/                        ← 現有 adapters/ 移入
│   │   ├── base.py                      ← 新增：BaseAdapter 抽象介面
│   │   ├── factory.py                   ← 新增：統一工廠（消除重複初始化）
│   │   ├── openai_adapter.py
│   │   ├── gemini_adapter.py
│   │   └── claude_adapter.py
│   │
│   ├── nlp/                             ← 從 adapters/__init__.py 拆出
│   │   ├── __init__.py
│   │   ├── tokenizer.py                 ← 多語系 tokenization
│   │   └── tool_selector.py             ← 動態工具注入邏輯
│   │
│   ├── integrations/                    ← 第三方整合層
│   │   └── line_connector.py            ← 修正 import，改用 Depends 注入
│   │
│   ├── dependencies/                    ← FastAPI 依賴注入容器
│   │   ├── __init__.py
│   │   ├── uma.py                       ← get_uma() 單例
│   │   ├── session.py                   ← get_session_manager() 單例
│   │   └── retriever.py                 ← get_retriever() 單例
│   │
│   └── schemas/                         ← Pydantic 資料模型（從 router.py 提取）
│       ├── chat.py
│       ├── skills.py
│       └── documents.py
│
├── frontend/                            ← static/ 改名並重組
│   ├── index.html                       ← 維持根層（服務靜態檔入口）
│   ├── src/
│   │   ├── js/
│   │   │   ├── modules/
│   │   │   │   ├── chat.js              ← app.js Module A 拆出
│   │   │   │   ├── skills.js            ← app.js Module B 拆出
│   │   │   │   ├── documents.js         ← 文件上傳/管理邏輯拆出
│   │   │   │   └── ui.js               ← Modal、Toast、工具函數拆出
│   │   │   ├── api.js                   ← 所有 fetch/API call 統一管理
│   │   │   └── i18n.js                 ← 現有 i18n.js 移入
│   │   └── css/
│   │       ├── tokens.css              ← 從 style.css 拆出：CSS Variables
│   │       ├── components.css          ← 從 style.css 拆出：元件樣式
│   │       ├── layout.css              ← 從 style.css 拆出：三欄佈局
│   │       └── dark.css                ← 新增：深色模式覆寫
│   └── assets/
│       └── images/                     ← 圖片資源
│
├── Agent_skills/                        ← 維持現有（git submodule，不改名）
│   ├── .git/                            ← submodule 倉庫
│   ├── skills_manifest.json
│   ├── shared/
│   └── skills/
│       └── mcp-*/（21 個）
│
├── docs/                                ← 新建：文件統一存放
│   ├── architecture/                    ← 根目錄 11 份 .md 移入
│   └── reports/                         ← report/ 目錄移入
│
├── tests/                               ← 新建：測試統一存放
│   ├── test_phase1.py                   ← 從根目錄移入
│   └── test_line_webhook.py             ← 從 scripts/ 移入
│
├── scripts/                             ← 維持，移入開發工具
│   ├── init_skill.py
│   ├── package_skill.py
│   ├── patch_versions.py
│   └── start_server.bat                 ← 從根目錄移入
│
├── memory/                              ← 維持（執行期寫入）
├── workspace/                           ← 維持（使用者文件）
├── tmp/                                 ← 維持（暫存）
│
├── .env                                 ← 維持
├── .env.template                        ← 修正 SKILLS_HOME 路徑
├── .gitmodules                          ← 維持（Agent_skills submodule）
├── Dockerfile                           ← 更新 WORKDIR/COPY 路徑
├── docker-compose.yml                   ← 同步路徑
└── requirements.txt
```

### 4.2 廢棄清單

| 路徑 | 處置 | 前置條件 |
|------|------|---------|
| `static/` | 重組為 `frontend/`，保留所有檔案內容 | `frontend/` 結構建立後 |
| `skills_backup/` | 直接刪除 | 確認 21 個現役技能完整 |
| `tmp/` | 清空後保留（或加入 .gitignore） | 確認無必要暫存 |
| 根目錄 11 份 `.md` | 移入 `docs/architecture/` | 建立 docs/ 目錄後 |
| `report/` | 移入 `docs/reports/` | 建立 docs/ 目錄後 |
| `test_phase1.py`（根目錄）| 移入 `tests/` | 建立 tests/ 目錄後 |
| `uma_demo.py`（根目錄）| 移入 `scripts/` 或 `tests/` | — |
| `start_server.bat`（根目錄）| 移入 `scripts/` | — |

---

## 5. 分階段執行計畫

### Phase 0：清理與搬移（1 天，零風險）

**目標：** 整理根目錄，不動任何功能程式碼。

```bash
# 建立新目錄
mkdir docs/architecture docs/reports tests

# 搬移文件（11 份 .md）
mv *.md docs/architecture/
mv report/ docs/reports/

# 搬移測試/開發腳本
mv test_phase1.py tests/
mv uma_demo.py scripts/
mv start_server.bat scripts/

# 刪除廢棄資源
rm -rf skills_backup/
```

**驗證：** 根目錄應只剩 `.env`、`.env.template`、`Dockerfile`、`docker-compose.yml`、`requirements.txt`、`.git*`、以及各功能目錄。

---

### Phase 1：修正設定不一致（0.5 天）

**目標：** 解決 `.env.template` 與 `docker-compose.yml` 路徑衝突（問題 C）。

**Step 1-A：確認 `main.py` 實際讀取的路徑**

```python
# main.py / uma_core.py 中：
SKILLS_HOME = os.getenv("SKILLS_HOME", "Agent_skills/skills")
# ↑ 實際預設值是 Agent_skills/skills
```

**Step 1-B：統一三處設定**

```bash
# .env.template：修正為與 docker-compose 一致
SKILLS_HOME=Agent_skills/skills

# docker-compose.yml：確認 volume 掛載正確
./Agent_skills:/app/Agent_skills   # ✓ 維持現有

# .env（執行中）：同步更新
SKILLS_HOME=Agent_skills/skills
```

**Step 1-C：更新 Dockerfile CMD**

```dockerfile
# 目前：uvicorn router:app（直接啟動 router.py）
# 搬移後應為：uvicorn server.app:app
# Phase 1 暫不更動，Phase 2 完成後再改
```

---

### Phase 2：後端目錄結構重組（3-4 天）

**目標：** 建立 `server/` 層次，搬移 `core/` 和 `adapters/`，拆分 `router.py`。

#### Step 2-A：建立 `server/` 骨架

```bash
mkdir -p server/routes server/core server/adapters
mkdir -p server/nlp server/integrations server/dependencies server/schemas
touch server/__init__.py server/routes/__init__.py
```

#### Step 2-B：搬移 `core/` → `server/core/`

搬移後需更新所有引用方的 import 路徑：

| 舊 import | 新 import |
|-----------|-----------|
| `from core.uma_core import UMA` | `from server.core.uma_core import UMA` |
| `from core.session import SessionManager` | `from server.core.session import SessionManager` |
| `from core.retriever import DocumentRetriever` | `from server.core.retriever import DocumentRetriever` |

**需更新的檔案：** `main.py`、`router.py`、`auto_agent.py`、`line_connector.py`

#### Step 2-C：搬移 `adapters/` → `server/adapters/`，抽出 NLP 邏輯

1. 搬移三個 adapter 檔案
2. 將 `adapters/__init__.py` 中的 `extract_tags()`、`select_relevant_tools()` 移至 `server/nlp/`：

```python
# server/nlp/tokenizer.py
def extract_tags(text: str) -> list[str]:
    """多語系 tokenization：jieba（中文）/ regex（日文）/ split（英文）"""
    ...

# server/nlp/tool_selector.py
def select_relevant_tools(query: str, tools: list, stop_words: set) -> list:
    """基於 query 動態注入相關技能工具"""
    ...
```

#### Step 2-D：建立依賴注入容器（解決問題 B）

```python
# server/dependencies/uma.py
from functools import lru_cache
from server.core.uma_core import UMA

_uma_instance: UMA | None = None

def get_uma() -> UMA:
    global _uma_instance
    if _uma_instance is None:
        _uma_instance = UMA()
        _uma_instance.initialize()
    return _uma_instance
```

```python
# server/dependencies/session.py
from functools import lru_cache
from server.core.session import SessionManager

@lru_cache(maxsize=1)
def get_session_manager() -> SessionManager:
    return SessionManager()
```

#### Step 2-E：修正 `line_connector.py` 緊耦合

```python
# 修正前：
from main import get_uma
from router import _session_mgr

# 修正後（server/integrations/line_connector.py）：
from server.dependencies.uma import get_uma
from server.dependencies.session import get_session_manager

# 路由中使用 FastAPI Depends：
@router.post("/api/line/webhook")
async def line_webhook(
    request: Request,
    uma: UMA = Depends(get_uma),
    session_mgr: SessionManager = Depends(get_session_manager)
):
    ...
```

#### Step 2-F：拆分 `router.py`（最複雜步驟）

| 新檔案 | 路由範圍 | 估計行數 |
|--------|---------|---------|
| `server/routes/chat.py` | `/chat`、`/chat/flush/*`、`/chat/session/*`、`/execute` | ~400 行 |
| `server/routes/documents.py` | `/api/documents/*`、`/api/research` | ~450 行 |
| `server/routes/skills.py` | `/skills/*`（含 CRUD、rescan、create、files） | ~500 行 |
| `server/routes/models.py` | `/api/models`、`/health` | ~100 行 |
| `server/app.py` | FastAPI 初始化、middleware、router 掛載 | ~100 行 |
| `server/schemas/*.py` | Pydantic 資料模型（從 router.py 提取）| ~150 行 |

**拆分後路由掛載：**

```python
# server/app.py
from fastapi import FastAPI
from server.routes import chat, documents, skills, models
from server.integrations.line_connector import router as line_router

app = FastAPI(title="MCP Skill Server")
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(skills.router)
app.include_router(models.router)
app.include_router(line_router)
```

#### Step 2-G：新增 Adapter Factory

```python
# server/adapters/factory.py
from server.core.uma_core import UMA
from server.adapters.openai_adapter import OpenAIAdapter
from server.adapters.gemini_adapter import GeminiAdapter
from server.adapters.claude_adapter import ClaudeAdapter

def create_adapter(model_name: str, uma: UMA):
    if model_name.startswith("gpt"):
        return OpenAIAdapter(uma)
    elif "gemini" in model_name:
        return GeminiAdapter(uma)
    elif "claude" in model_name:
        return ClaudeAdapter(uma)
    raise ValueError(f"Unknown model: {model_name}")
```

#### Step 2-H：更新 Dockerfile 入口

```dockerfile
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

### Phase 3：前端重組（2-3 天）

**目標：** `static/` → `frontend/`，拆分 `app.js`（104 KB），重組 CSS。

#### Step 3-A：建立 `frontend/` 結構

```bash
mkdir -p frontend/src/js/modules frontend/src/css frontend/assets/images
```

#### Step 3-B：拆分 `app.js`（104 KB → 5 個模組）

依照現有 Module A / Module B 的嚴格分離原則，進一步細化：

| 新模組 | 來源 | 職責 |
|--------|------|------|
| `src/js/modules/chat.js` | Module A | LLM 對話、Streaming 顯示、Markdown 渲染 |
| `src/js/modules/skills.js` | Module B | 技能清單、CRUD、技能抽屜 UI |
| `src/js/modules/documents.js` | Module A/B 共用 | 文件上傳、Workspace 預覽、SHA 去重 |
| `src/js/modules/ui.js` | 全域 | Modal、Toast、Alert、工具函數 |
| `src/js/api.js` | 散落各處 | 所有 fetch call 統一管理 |

**`api.js` 範例（統一管理 API 呼叫）：**

```javascript
// frontend/src/js/api.js
const API_BASE = '';

export async function fetchModels() {
    return fetch(`${API_BASE}/api/models`).then(r => r.json());
}

export async function sendChatMessage(payload) {
    return fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
}

export async function uploadDocument(formData, onProgress) { ... }
export async function listSkills() { ... }
export async function rescanSkills() { ... }
```

#### Step 3-C：拆分 `style.css`（56 KB → 4 個分層）

```css
/* frontend/src/css/tokens.css — 設計 Token（CSS Variables）*/
:root {
  --color-brand-blue: #18409b;
  --color-brand-orange: #f68300;
  --radius-xs: 4px;
  /* ... */
}

/* frontend/src/css/dark.css — 深色模式（新增）*/
@media (prefers-color-scheme: dark) {
  :root {
    --bg-base: #0d0d0d;
    --bg-surface: #1c1c1e;
    --text-primary: #f5f5f7;
    --text-secondary: #ebebf599;
    --border-subtle: rgba(255,255,255,0.08);
  }
}

/* frontend/src/css/components.css — 元件樣式 */
/* frontend/src/css/layout.css — 三欄佈局結構 */
```

#### Step 3-D：`index.html` 引用更新

```html
<!-- 舊：<link rel="stylesheet" href="style.css?v=27"> -->
<link rel="stylesheet" href="src/css/tokens.css">
<link rel="stylesheet" href="src/css/components.css">
<link rel="stylesheet" href="src/css/layout.css">
<link rel="stylesheet" href="src/css/dark.css">

<!-- 舊：<script src="app.js?v=..."></script> -->
<script type="module" src="src/js/api.js"></script>
<script type="module" src="src/js/modules/ui.js"></script>
<script type="module" src="src/js/modules/chat.js"></script>
<script type="module" src="src/js/modules/skills.js"></script>
<script type="module" src="src/js/modules/documents.js"></script>
```

#### Step 3-E：更新 Dockerfile 靜態檔路徑

```dockerfile
# 舊：COPY static/ ./static/
COPY frontend/ ./frontend/
```

```python
# server/app.py 靜態檔掛載
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
```

---

## 6. 風險與注意事項

### 高風險項目

| 項目 | 風險說明 | 緩解措施 |
|------|---------|---------|
| `router.py` 拆分 | 路由間有共用的 `_delta_index_skills()`、hash registry 等邏輯 | 先提取共用邏輯至 `server/core/` 再拆路由 |
| `line_connector.py` 修正 | 改用 Depends 後，LINE SDK 懶加載邏輯需重構 | 先在測試環境驗證 LINE webhook 仍可接收 |
| `Agent_skills/` 子模組 | 有獨立 `.git`，不可直接搬移，需使用 git submodule 指令管理 | `git submodule status` 確認後再決定策略 |
| `app.js` 拆分 | 104 KB 中模組邊界需仔細識別，避免循環依賴 | 先讀完整個 app.js 再規劃拆分邊界 |
| Docker volume 路徑 | Phase 2 後 `server/` 路徑改變，需同步更新 compose | 每個 Phase 後都驗證 Docker 啟動正常 |

### 建議不要動的部分

| 理由 | 項目 |
|------|------|
| 業務邏輯穩定，職責單一 | `core/` 六個模組（搬移但不改內容） |
| 技能資料是執行期資產 | `Agent_skills/skills/mcp-*/` 內容 |
| 執行期寫入，不是程式碼 | `memory/`、`workspace/` |
| submodule 需獨立處理 | `Agent_skills/.git` |

### 執行建議順序

```
Phase 0（清理根目錄）
    ↓
Phase 1（修正設定不一致）
    ↓
Phase 2-A/B（建 server/ 骨架 + 搬移 core/adapters/）
    ↓               ↓（可平行）
Phase 2-C~F     Phase 3-A/B/C（前端重組）
（router 拆分）   （app.js + CSS 拆分）
    ↓               ↓
Phase 2-G/H     Phase 3-D/E
（Factory + Docker）（index.html + Dockerfile）
    ↓
整合測試（Docker 啟動 + LINE webhook + 對話功能）
```

**Phase 2 的路由拆分與 Phase 3 的前端重組互相獨立，可指派不同人同步進行。**

---

## 附錄：各版本報告修訂說明

| v1 | v2 | v3（本版）|
|----|----|----|
| 以 AgentPortal 為前端參考 | 同左 | AgentPortal 已不存在，改以 static/ 為唯一前端 |
| 建議 AgentPortal → portal/ | 建議整合雙 UI | 建議 static/ → frontend/ 並重組 |
| 後端移入 server/ | 新增 routes/ 拆分 + dependencies/ | 新增 nlp/ 模組、schemas/、完整拆分方案 |
| 未提及設定不一致 | 初步提及 | 新增 Phase 1 專門修正 .env + docker-compose 不一致 |
| 未提及 Agent_skills 是 submodule | 建議改名為 skills/ | 確認是 submodule，建議維持現有不改名 |
| 未分析 app.js | 未分析 app.js | 新增 app.js 104KB 問題及拆分策略 |

---

*本報告基於 2026-03-12 實際掃描結果，AgentPortal/ 已確認不存在。所有建議均基於 static/ 為唯一前端的現況。*
