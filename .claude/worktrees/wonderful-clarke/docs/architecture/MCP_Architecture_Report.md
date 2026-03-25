# MCP Server & 跨模型共享架構開發報告

## 1. 執行摘要 (Executive Summary)
本報告旨在規劃一套標準化的 **Agent & Skills 集中管理系統**。核心策略為利用 **GitHub** 作為唯一事實來源 (Source of Truth)，並透過 **Model Context Protocol (MCP)** 建立一個通用的連接層，使不同語言模型 (Gemini, GPT, Claude) 與不同作業系統 (Windows, macOS, Linux) 均能以一致的方式調用擴展技能。

---

## 2. MCP Server 架構設計
採用 **MCP (Model Context Protocol)** 核心協議，將「資源 (Resources)」、「工具 (Tools)」與「提示 (Prompts)」解耦。

### 核心組件：
*   **MCP Skill Hub (GitHub)**：集中管理所有技能的原始碼、`.md` 說明文件與元數據。建議採單一 Repo (Monorepo) 或特定 GitHub Orgs 下的多個 Repos。
*   **MCP Server 核心層**：負責轉譯 GitHub 上的技能定義為各模型可識別的工具格式。支援動態熱更新。
*   **引用模式 (Reference Pattern)**：
    *   開發者在本地 `agent-config.json` 中宣告依賴：`"skills": ["owner/repo@version"]`。
    *   系統自動處理依賴遞歸加載，確保「引用而非複製」。
*   **動態裝載器 (Dynamic Loader)**：系統啟動時根據設定檔自動從 GitHub 更新/同步技能包，實現「一處修改，多處同步」。

---

## 3. 跨模型共享與 API 串接架構
為了解決不同 LLM 對於工具調用 (Tool Calling) 格式的差異，建立 **Unified Model Adapter (UMA)** 層。

### 串接流向：
1.  **模型發起請求**：模型產生 JSON 格式的工具調用。
2.  **UMA 轉譯**：Adapter 將 GPT/Gemini/Claude 的原始請求轉發至 MCP Server。
3.  **流式處理規範 (Streaming Access)**：強制規定 `Scripts/` 下處理大檔案的工具必須支援流式讀取，禁止將大檔案直接返回給 UMA 層，需分段或以指標形式傳遞。
4.  **索引預處理**：對於大型 `References/`，要求模型優先調用 `grep` 或索引搜索工具，而非讀取全文，以節省 Token。
5.  **MCP 執行**：Server 執行對應的 Skill 程式碼並回傳結果。
6.  **跨模型適配**：回傳結果經由 UMA 標準化後再送回 LLM。

---

## 4. 技術規格標準化 (Technical Standards)

### 4.1 路徑標準化 (Path Standardization)
*   **相對路徑優先 (Relative Path First)**：在 `SKILL.md` 的指令建議中，明確告知模型使用相對於 Skill 根目錄的「相對路徑」來調用 `Scripts/`。這能簡化跨作業系統的路徑轉譯，並強化模型對檔案結構的理解。
*   **環境變數驅動**：所有路徑使用基礎變數（如 `${SKILLS_HOME}`）而非絕對路徑。
*   **POSIX 相容性**：檔案路徑在代碼層級統一使用 `/`，並透過 Node.js `path` 或 Python `pathlib` 在運行時自動處理轉義，確保 Windows 與 Unix-like 系統無縫切換。

### 4.2 Token 經濟使用 (Token Economy)
*   **階層式讀取策略**：模型應嚴格遵循「中繼資料優先」流程：
    1.  讀取 `SKILL.md` 的 YAML Frontmatter (Metadata)。
    2.  判定需觸發後才載入正文。
    3.  最後才依需讀取 `References`、`Scripts` 及 `Assets`。
*   **低自由度模式 (Strict Mode)**：強制執行特定腳本、極少參數的調用規範。
    *   **輸入邊界檢查 (Boundary Checking)**：`Scripts/` 內的所有運算單元必須具備嚴格的參數校驗（如長度、類型、正規表示式限制），防止模型注入過大或無效的參數導致系統崩潰或 Token 浪費。
*   **摘要定義 (Skeleton Definition)**：向模型註冊工具時，僅傳送 API 描述與輸入 schema。
*   **語義快取 (Semantic Caching)**：對重複性高或時效長的操作結果進行快取。

### 4.4 環境依賴與運行時管理
*   **`runtime_requirements`**：在 `SKILL.md` 元數據中定義依賴項 (如：`python-docx`, `pandas`)。
*   **環境預檢**：MCP Server 動態裝載時自動檢核環境，缺漏時報錯或依據安全性原則自動執行安裝。

---

## 5. GitHub 集中管理流程 (Skill Hub)
採用標準化的 **"Skill Bundle"** 四層架構，並導入自動化驗證與規範命名：

*   **規格命名規則**：採用 `[provider]-[tool-name]` 格式 (例如 `mcp-file-processor`)。目錄名稱必須與 `SKILL.md` 中的 `name` 欄位完全一致樣。
*   **四層架構細節**：
    *   **`SKILL.md` (描述層)**：存放 MCP 元數據與 YAML Frontmatter。
    *   **`References/` (參考層)**：知識庫，支援 `grep` 模式搜索。
    *   **`Scripts/` (操作層)**：具備流式處理能力的執行腳本。
    *   **`Assets/` (素材層)**：模板與資源。
*   **自動化腳本 (OpenClaw Standard)**：
    *   `init_skill.py`：初始化 Skill 目錄結構。
    *   `package_skill.py`：由 CI/CD 觸發，執行結構驗證 (Validation) 與打包上傳。

---

## 6. 結論與下一步建議
此架構確保了 Agent 開發的高複用性與安全性。後續可優先開發 **MCP Registry CLI**，用於自動處理多平台下的環境安裝與技能同步。
