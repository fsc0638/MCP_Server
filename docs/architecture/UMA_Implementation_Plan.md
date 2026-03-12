# UMA (Unified Model Adapter) 實作計畫

UMA 是連接 **GitHub Skills** 與 **LLM (Gemini/OpenAI/Claude)** 的核心橋樑。其目標是將 Skill Bundle 中的元數據轉換為模型可識別的「工具定義 (Tool Definition)」，並動態執行腳本。

## 1. 核心組件設計

### 1.1 `SkillRegistry` (技能註冊表)
*   **多重快取機制 (Caching)**：
    *   **Schema Cache**：緩存轉譯後的工具定義，避免重複解析 YAML。支援 **版本鎖定 (Version Pinning)**，記錄 Git Hash/Tag，防止代碼更新但緩存未同步導致參數不匹配。
    *   **Validation Cache**：標記已信任的 Skills。
    *   **Result Cache**：語義結果快取。
*   **依賴自動校驗 (Dependency Validation)**：載入時檢查 `runtime_requirements`。若環境未就緒（如缺 pandas），標註「環境未就緒」標籤，並在工具描述中告知模型該工具目前不可用，避免無效調用。
*   負責解析 `SKILL.md` 的 YAML Metadata。

### 1.2 `SchemaConverter` (格式轉譯器)
*   **Token 裁剪邏輯 (Token Pruning)**：自動摘要過長的 `description`。
*   **自主性邏輯注入 (Resourceful Logic Injection)**：在轉換出的工具描述中加入：「在處理檔案前，請優先使用 `read_resource` 或 `search_resource` 查看參考規範」。
*   將標準 Metadata 轉譯為各模型對應格式。

### 1.3 `ExecutionEngine` (執行引擎)
*   **Context Injector**：執行時動態注入專案路徑、API Key 等環境變數。
*   **Resource Accessor (Enhanced)**：
    *   `read_resource`：讀取全文。
    *   **`search_resource` (Grep 模式)**：針對大檔案提供關鍵字搜尋，符合 Token 節約原則。
*   **數據指標與清理層 (Pointer & Cleanup)**：提供臨時數據路徑。實作 **Cleanup Job**，在 Session 結束或超時後自動刪除臨時數據，防止磁碟空間爆炸。
*   **標準化錯誤回傳**：區分 STDOUT 與 STDERR，引導模型修復。
*   **安全性強化 (Path Sanitization)**：執行前強制清理路徑，防止「目錄穿越攻擊」(Directory Traversal)，確保操作限制在 `${SKILLS_HOME}` 內。

## 2. 開發優先級 (Prioritized Roadmap)

### 2.1 高優先級 (High Priority)
*   **`ExecutionEngine` 安全性與路徑清理 (Path Sanitization)**：實作目錄穿越防護與邊界檢查。
*   **`SkillRegistry` 核層**：YAML 解析、Version Pinning、Dependency Validation 與快取實作。
*   **基礎流式回傳與大檔案搜尋**：支援 `search_resource` 模式。

### 2.2 中優先級 (Medium Priority)
*   **`SchemaConverter` 多模型適配**：Token 裁剪、Gemini/OpenAI/Claude 適配器。

### 2.3 低優先級 (Low Priority)
*   **自動化驗證工具整合**：在 UMA 載入時自動執行 `package_skill.py` 核驗。

---

## 3. 實作規劃
*   [NEW] [uma_core.py](file:///c:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/core/uma_core.py)：包含 Registry 與快取邏輯。
*   [NEW] [executor.py](file:///c:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/core/executor.py)：具備 Context Injector 的執行引擎。

---

## 4. 驗證計畫

### 自動化測試
*   使用 `mcp-sample-converter` 模擬工具調用流程。
*   驗證 UMA 轉換出的 JSON Schema 是否正確。

### 手動測試
*   透過 `dotenv` 載入金鑰，實際發送一次工具調用測試請求。
